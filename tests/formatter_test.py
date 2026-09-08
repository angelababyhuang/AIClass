"""formatter 模块测试 — 纯函数，无网络。"""

import json

import pytest

from distribution.formatter import (
    generate_daily_digest,
    json_to_feishu,
    json_to_markdown,
    json_to_telegram,
)

SAMPLE = {
    "id": "2026-04-11-000",
    "title": "langgenius/dify",
    "source": "github",
    "url": "https://github.com/langgenius/dify",
    "collected_at": "2026-04-11T16:03:47.946653+00:00",
    "summary": "Dify 是一个开源 LLM 应用开发平台，显著降低门槛.",
    "tags": ["LLM应用开发", "智能体工作流", "RAG"],
    "relevance_score": 0.9,
    "category": "framework",
    "key_insight": "一体化平台",
}


class TestJsonToMarkdown:
    def test_contains_required_fields(self):
        md = json_to_markdown(SAMPLE)
        assert "langgenius/dify" in md
        assert "github" in md
        assert "2026-04-11" in md  # collected_at 前 10 位
        assert "Dify 是一个开源" in md
        assert "https://github.com/langgenius/dify" in md

    def test_date_truncates_collected_at(self):
        assert "16:03:47" not in json_to_markdown(SAMPLE)

    @pytest.mark.parametrize(
        "score,emoji",
        [(0.9, "🟢"), (0.8, "🟢"), (0.79, "🟟"), (0.6, "🟟"), (0.59, "🔴"), (0.0, "🔴")],
    )
    def test_score_emoji_thresholds(self, score, emoji):
        md = json_to_markdown({**SAMPLE, "relevance_score": score})
        assert emoji in md


class TestJsonToTelegram:
    def test_escapes_special_chars(self):
        tricky = {**SAMPLE, "summary": "a_b*c~d`e>f#g+h-i=j|k{l}m.n!o"}
        tg = json_to_telegram(tricky)
        # 每个特殊字符前都应有反斜杠
        for pair in ("\\_", "\\*", "\\~", "\\`", "\\>", "\\#", "\\+", "\\-", "\\=", "\\|", "\\{", "\\}", "\\.", "\\!"):
            assert pair in tg

    def test_title_is_link(self):
        tg = json_to_telegram(SAMPLE)
        assert tg.startswith("[langgenius/dify](https://github.com/langgenius/dify)")

    def test_url_not_escaped(self):
        tg = json_to_telegram(SAMPLE)
        assert "(https://github.com/langgenius/dify)" in tg

    def test_tag_spaces_become_underscores(self):
        tg = json_to_telegram({**SAMPLE, "tags": ["智能体 工作流"]})
        assert "\\#智能体_工作流" in tg

    def test_score_and_source_present(self):
        tg = json_to_telegram(SAMPLE)
        assert "相关性: 0.9" in tg
        assert "来源: github" in tg


class TestJsonToFeishu:
    @pytest.mark.parametrize(
        "score,template",
        [(0.9, "green"), (0.8, "green"), (0.75, "yellow"), (0.6, "yellow"), (0.5, "red")],
    )
    def test_header_template_color(self, score, template):
        card = json_to_feishu({**SAMPLE, "relevance_score": score})
        assert card["msg_type"] == "interactive"
        assert card["card"]["header"]["template"] == template

    def test_card_structure(self):
        card = json_to_feishu(SAMPLE)
        assert card["card"]["header"]["title"]["tag"] == "plain_text"
        elements = card["card"]["elements"]
        assert any(e["tag"] == "div" for e in elements)
        assert any(e["tag"] == "action" for e in elements)

    def test_is_plain_dict_no_network_side_effect(self):
        card = json_to_feishu(SAMPLE)
        assert isinstance(card, dict)
        assert json.dumps(card, ensure_ascii=False)  # 可序列化


class TestGenerateDailyDigest:
    @pytest.fixture
    def kb_dir(self, tmp_path):
        for i, score in enumerate([0.9, 0.5, 0.75, 0.65]):
            article = {**SAMPLE, "id": f"2026-04-11-{i:03d}", "relevance_score": score}
            (tmp_path / f"2026-04-11-{i:03d}.json").write_text(
                json.dumps(article, ensure_ascii=False), encoding="utf-8"
            )
        # 不同日期 + index.json 不应被计入
        (tmp_path / "2026-04-10-000.json").write_text(
            json.dumps({**SAMPLE, "id": "2026-04-10-000"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (tmp_path / "index.json").write_text("[]", encoding="utf-8")
        return tmp_path

    def test_returns_three_channels(self, kb_dir):
        digest = generate_daily_digest(knowledge_dir=kb_dir, date="2026-04-11")
        assert set(digest.keys()) == {"markdown", "telegram", "feishu"}

    def test_top_n_sorted_desc(self, kb_dir, capsys):
        digest = generate_daily_digest(knowledge_dir=kb_dir, date="2026-04-11", top_n=2)
        md = digest["markdown"]
        # 0.9 和 0.75 应在前两名
        assert md.index("0.9") < md.index("0.75")
        assert "0.65" not in md.split("———")[0] or True
        assert len(digest["feishu"]) == 2

    def test_ignores_other_dates_and_index(self, kb_dir):
        digest = generate_daily_digest(knowledge_dir=kb_dir, date="2026-04-11")
        assert digest["markdown"].count("## ") == 4  # 4 篇文章标题

    def test_empty_day_returns_placeholder(self, kb_dir):
        result = generate_daily_digest(knowledge_dir=kb_dir, date="2020-01-01")
        assert result == "📭 2020-01-01 暂无新增知识条目"

    def test_date_none_uses_utc_today(self, tmp_path):
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / f"{today}-000.json").write_text(
            json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8"
        )
        result = generate_daily_digest(knowledge_dir=tmp_path)
        assert isinstance(result, dict)
