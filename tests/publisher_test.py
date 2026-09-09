"""publisher 模块测试 — 用本地回环 aiohttp 假服务器模拟 Telegram / 飞书 API。

不访问外网：所有请求打到 127.0.0.1 随机端口的测试服务器。
"""

import json

import pytest
import pytest_asyncio
from aiohttp import web

from distribution.publisher import (
    BasePublisher,
    FeishuPublisher,
    PublishResult,
    TelegramPublisher,
    _split_telegram,
    publish_daily_digest,
)

SAMPLE_ARTICLE = {
    "id": "2026-04-11-000",
    "title": "langgenius/dify",
    "source": "github",
    "url": "https://github.com/langgenius/dify",
    "collected_at": "2026-04-11T16:03:47.946653+00:00",
    "summary": "Dify 是一个开源 LLM 应用开发平台。",
    "tags": ["LLM应用开发"],
    "relevance_score": 0.9,
    "category": "framework",
    "key_insight": "一体化平台",
}


@pytest_asyncio.fixture
async def fake_api():
    """启动本地假服务器，返回 (base_url, received_requests)."""
    received: list[tuple[str, dict]] = []

    async def telegram_ok(request: web.Request) -> web.Response:
        received.append(("telegram", await request.json()))
        return web.json_response({"ok": True, "result": {"message_id": 42}})

    async def telegram_fail(request: web.Request) -> web.Response:
        received.append(("telegram-fail", await request.json()))
        return web.json_response(
            {"ok": False, "description": "Bad Request: chat not found"}, status=400
        )

    async def feishu_ok(request: web.Request) -> web.Response:
        received.append(("feishu", await request.json()))
        return web.json_response({"code": 0, "msg": "success"})

    async def feishu_fail(request: web.Request) -> web.Response:
        received.append(("feishu-fail", await request.json()))
        return web.json_response({"code": 19021, "msg": "sign match fail"})

    app = web.Application()
    app.router.add_post("/tg/bot{T}/sendMessage", telegram_ok)
    app.router.add_post("/tgfail/bot{T}/sendMessage", telegram_fail)
    app.router.add_post("/fs", feishu_ok)
    app.router.add_post("/fsfail", feishu_fail)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001

    yield f"http://127.0.0.1:{port}", received

    await runner.cleanup()


# ---------------------------------------------------------------------------
# PublishResult / 基类
# ---------------------------------------------------------------------------

class TestPublishResult:
    def test_defaults(self):
        r = PublishResult(channel="feishu", success=True)
        assert r.message_id is None
        assert r.error is None

    def test_base_publisher_is_abstract(self):
        with pytest.raises(TypeError):
            BasePublisher()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# TelegramPublisher
# ---------------------------------------------------------------------------

class TestTelegramPublisher:
    @pytest.mark.asyncio
    async def test_send_message_success(self, fake_api):
        base, received = fake_api
        p = TelegramPublisher(bot_token="T", chat_id="C", api_base=f"{base}/tg")
        r = await p.send_message({"text": "hello", "parse_mode": "MarkdownV2"})
        assert r.success and r.message_id == "42"
        body = received[0][1]
        assert body["chat_id"] == "C"
        assert body["parse_mode"] == "MarkdownV2"

    @pytest.mark.asyncio
    async def test_api_error_reported(self, fake_api):
        base, _ = fake_api
        p = TelegramPublisher(bot_token="T", chat_id="C", api_base=f"{base}/tgfail")
        r = await p.send_message({"text": "x"})
        assert not r.success
        assert "chat not found" in (r.error or "")

    @pytest.mark.asyncio
    async def test_missing_env_returns_failure_without_request(self, fake_api):
        base, received = fake_api
        p = TelegramPublisher(bot_token=None, chat_id=None, api_base=f"{base}/tg")
        r = await p.send_message({"text": "x"})
        assert not r.success
        assert "TELEGRAM_BOT_TOKEN" in (r.error or "")
        assert received == []

    @pytest.mark.asyncio
    async def test_digest_placeholder_sent_without_parse_mode(self, fake_api):
        base, received = fake_api
        p = TelegramPublisher(bot_token="T", chat_id="C", api_base=f"{base}/tg")
        r = await p.send_digest("📭 2020-01-01 暂无新增知识条目")
        assert r.success
        assert "parse_mode" not in received[0][1]

    def test_split_telegram(self):
        short = "a" * 100
        assert _split_telegram(short) == [short]
        long_text = "\n\n———\n\n".join(["b" * 2000] * 4)
        chunks = _split_telegram(long_text)
        assert all(len(c) <= 4000 for c in chunks)
        assert "".join(chunks).replace("\n\n———\n\n", "") .count("b") == 8000


# ---------------------------------------------------------------------------
# FeishuPublisher
# ---------------------------------------------------------------------------

class TestFeishuPublisher:
    @pytest.mark.asyncio
    async def test_send_card_success(self, fake_api):
        base, received = fake_api
        p = FeishuPublisher(webhook_url=f"{base}/fs")
        card = {"msg_type": "interactive", "card": {"elements": []}}
        r = await p.send_message(card)
        assert r.success
        assert received[0][1] == card

    @pytest.mark.asyncio
    async def test_webhook_error_code(self, fake_api):
        base, _ = fake_api
        p = FeishuPublisher(webhook_url=f"{base}/fsfail")
        r = await p.send_message({"msg_type": "text", "content": {"text": "x"}})
        assert not r.success
        assert "19021" in (r.error or "")

    @pytest.mark.asyncio
    async def test_missing_env(self, monkeypatch):
        monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
        p = FeishuPublisher(webhook_url=None)
        r = await p.send_message({"msg_type": "text"})
        assert not r.success
        assert "FEISHU_WEBHOOK_URL" in (r.error or "")


# ---------------------------------------------------------------------------
# publish_daily_digest 统一入口
# ---------------------------------------------------------------------------

class TestPublishDailyDigest:
    @pytest.fixture
    def kb_dir(self, tmp_path):
        for i, score in enumerate([0.9, 0.7]):
            article = {
                **SAMPLE_ARTICLE,
                "id": f"2026-04-11-{i:03d}",
                "relevance_score": score,
            }
            (tmp_path / f"2026-04-11-{i:03d}.json").write_text(
                json.dumps(article, ensure_ascii=False), encoding="utf-8"
            )
        return tmp_path

    @pytest.mark.asyncio
    async def test_concurrent_publish_both_channels(self, fake_api, kb_dir):
        base, received = fake_api
        results = await publish_daily_digest(
            knowledge_dir=kb_dir,
            date="2026-04-11",
            publishers=[
                TelegramPublisher(bot_token="T", chat_id="C", api_base=f"{base}/tg"),
                FeishuPublisher(webhook_url=f"{base}/fs"),
            ],
        )
        assert [r.channel for r in results] == ["telegram", "feishu"]
        assert all(r.success for r in results)
        feishu_cards = [b for ch, b in received if ch == "feishu"]
        assert len(feishu_cards) == 2
        assert feishu_cards[0]["msg_type"] == "interactive"
        telegram_bodies = [b for ch, b in received if ch == "telegram"]
        assert telegram_bodies[0]["parse_mode"] == "MarkdownV2"
        assert "2026\\-04\\-11" in telegram_bodies[0]["text"]  # MarkdownV2 转义后的日期

    @pytest.mark.asyncio
    async def test_unconfigured_channels_fail_gracefully(self, kb_dir, monkeypatch):
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FEISHU_WEBHOOK_URL"):
            monkeypatch.delenv(var, raising=False)
        results = await publish_daily_digest(knowledge_dir=kb_dir, date="2026-04-11")
        assert len(results) == 2
        assert all(not r.success for r in results)

    @pytest.mark.asyncio
    async def test_empty_day_publishes_placeholder(self, fake_api, kb_dir):
        base, received = fake_api
        results = await publish_daily_digest(
            knowledge_dir=kb_dir,
            date="2020-01-01",
            publishers=[
                TelegramPublisher(bot_token="T", chat_id="C", api_base=f"{base}/tg"),
                FeishuPublisher(webhook_url=f"{base}/fs"),
            ],
        )
        assert all(r.success for r in results)
        assert "📭 2020-01-01" in received[0][1]["text"]
        assert received[1][1]["msg_type"] == "text"
