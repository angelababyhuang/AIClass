"""distribution/publisher.py — 多渠道推送模块（网络层）。

与 formatter.py 分工：formatter 只做纯格式化，publisher 负责真正的网络投递。
架构：
    BasePublisher          抽象基类，定义 send_message / send_digest 接口
    TelegramPublisher      Telegram Bot API，异步发送 MarkdownV2 消息
    FeishuPublisher        飞书自定义机器人 Webhook，发送 interactive 卡片
    PublishResult          每次发布的结果数据类
    publish_daily_digest   统一异步入口：生成日报并并发发布到所有渠道

所有配置从环境变量读取，缺失时不抛异常而是返回失败的 PublishResult，
保证多渠道场景下（如只配了飞书）其余渠道可正常运行。
"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import aiohttp
from dotenv import load_dotenv

from distribution.formatter import generate_daily_digest

load_dotenv()

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_TIMEOUT_SECONDS = 30.0


async def _read_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """读取响应体并尽量解析为 dict（非 JSON 错误页降级为文本片段）。

    Args:
        resp: aiohttp 响应对象。

    Returns:
        解析后的 dict；解析失败时返回 {"raw": 前 200 字符}。
    """
    text = await resp.text()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"raw": str(parsed)[:200]}
    except ValueError:
        return {"raw": text[:200]}


@dataclass
class PublishResult:
    """单次发布的结果。

    Attributes:
        channel: 渠道名（"telegram" / "feishu"）。
        success: 是否成功。
        message_id: 渠道返回的消息 ID；飞书 webhook 不返回时为 None。
        error: 失败原因；成功时为 None。
    """

    channel: str
    success: bool
    message_id: str | None = None
    error: str | None = None


class BasePublisher(ABC):
    """推送渠道抽象基类。

    子类需实现 channel 类属性与 send_message / send_digest 两个接口。
    """

    channel: str = "base"

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @abstractmethod
    async def send_message(self, payload: dict[str, Any]) -> PublishResult:
        """发送单条已格式化好的消息体。

        Args:
            payload: 渠道专属的消息体（Telegram 为 text 字段，飞书为卡片 dict）。

        Returns:
            PublishResult 发布结果。
        """

    @abstractmethod
    async def send_digest(self, digest: dict[str, Any] | str) -> PublishResult:
        """发送一整份日报。

        Args:
            digest: generate_daily_digest 的返回值（dict），或空日期占位字符串。

        Returns:
            聚合后的 PublishResult（success 为全部子消息的与）。
        """

    def _aggregate(self, results: list[PublishResult]) -> PublishResult:
        """把多条子消息的结果聚合为一条。

        Args:
            results: 各子消息的发布结果。

        Returns:
            success 全真才为真；message_id 逗号拼接；error 取第一条失败原因。
        """
        ok = all(r.success for r in results) if results else False
        ids = [r.message_id for r in results if r.message_id]
        first_error = next((r.error for r in results if not r.success), None)
        return PublishResult(
            channel=self.channel,
            success=ok,
            message_id=",".join(ids) if ids else None,
            error=first_error,
        )


def _split_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT - 96) -> list[str]:
    """按日报分隔符把长文本切分为不超过 Telegram 上限的片段。

    Args:
        text: 完整日报文本。
        limit: 单片段最大长度（默认给转义膨胀留出余量）。

    Returns:
        片段列表；任何单段超限时在字符级硬切。
    """
    if len(text) <= limit:
        return [text]

    parts = text.split("\n\n———\n\n")
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = part if not current else current + "\n\n———\n\n" + part
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = part
    if current:
        chunks.append(current)

    return [c[i : i + limit] for c in chunks for i in range(0, len(c), limit)]


class TelegramPublisher(BasePublisher):
    """Telegram Bot API 渠道。

    配置来自环境变量 TELEGRAM_BOT_TOKEN 与 TELEGRAM_CHAT_ID。
    """

    channel = "telegram"

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        api_base: str = TELEGRAM_API_BASE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(timeout)
        self._bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._api_base = api_base.rstrip("/")

    async def send_message(self, payload: dict[str, Any]) -> PublishResult:
        if not self._bot_token or not self._chat_id:
            return PublishResult(
                channel=self.channel,
                success=False,
                error="缺少环境变量 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳过",
            )

        url = f"{self._api_base}/bot{self._bot_token}/sendMessage"
        data: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": payload.get("text", ""),
        }
        if payload.get("parse_mode"):
            data["parse_mode"] = payload["parse_mode"]

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, json=data) as resp:
                    body = await _read_json(resp)
                    if resp.status == 200 and body.get("ok"):
                        return PublishResult(
                            channel=self.channel,
                            success=True,
                            message_id=str(body["result"]["message_id"]),
                        )
                    return PublishResult(
                        channel=self.channel,
                        success=False,
                        error=f"HTTP {resp.status}: {body.get('description') or body}",
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return PublishResult(channel=self.channel, success=False, error=str(e))

    async def send_digest(self, digest: dict[str, Any] | str) -> PublishResult:
        if isinstance(digest, str):
            chunks, parse_mode = [digest], None
        else:
            chunks, parse_mode = _split_telegram(digest["telegram"]), "MarkdownV2"

        results = [
            await self.send_message({"text": chunk, "parse_mode": parse_mode})
            for chunk in chunks
        ]
        return self._aggregate(results)


class FeishuPublisher(BasePublisher):
    """飞书自定义机器人 Webhook 渠道。

    配置来自环境变量 FEISHU_WEBHOOK_URL。
    """

    channel = "feishu"

    def __init__(
        self,
        webhook_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(timeout)
        self._webhook_url = webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")

    async def send_message(self, payload: dict[str, Any]) -> PublishResult:
        if not self._webhook_url:
            return PublishResult(
                channel=self.channel,
                success=False,
                error="缺少环境变量 FEISHU_WEBHOOK_URL，跳过",
            )

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(self._webhook_url, json=payload) as resp:
                    body = await _read_json(resp)
                    if resp.status == 200 and body.get("code") == 0:
                        return PublishResult(channel=self.channel, success=True)
                    return PublishResult(
                        channel=self.channel,
                        success=False,
                        error=f"code={body.get('code')}: {body.get('msg') or body}",
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return PublishResult(channel=self.channel, success=False, error=str(e))

    async def send_digest(self, digest: dict[str, Any] | str) -> PublishResult:
        if isinstance(digest, str):
            placeholder = {
                "msg_type": "text",
                "content": {"text": digest},
            }
            return await self.send_message(placeholder)

        results = [
            await self.send_message(card) for card in digest["feishu"]
        ]
        return self._aggregate(results)


async def publish_daily_digest(
    knowledge_dir: str = "knowledge/articles",
    date: str | None = None,
    top_n: int = 5,
    publishers: list[BasePublisher] | None = None,
) -> list[PublishResult]:
    """统一异步入口：生成日报并并发发布到所有渠道。

    Args:
        knowledge_dir: 知识条目目录，传给 generate_daily_digest。
        date: 日期 YYYY-MM-DD；None 取 UTC 今天。
        top_n: 每渠道最多发送的相关性 Top N 条。
        publishers: 发布器列表；None 时默认 [TelegramPublisher, FeishuPublisher]。
            未配置环境变量的渠道会返回失败的 PublishResult 而不中断其他渠道。

    Returns:
        各渠道的 PublishResult 列表（顺序与 publishers 一致）。
    """
    digest = generate_daily_digest(
        knowledge_dir=knowledge_dir, date=date, top_n=top_n
    )
    if publishers is None:
        publishers = [TelegramPublisher(), FeishuPublisher()]

    return list(await asyncio.gather(*(p.send_digest(digest) for p in publishers)))
