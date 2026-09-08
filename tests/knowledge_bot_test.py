"""knowledge_bot 模块测试 — 纯本地，无网络。"""

import json

import pytest

from bot.knowledge_bot import (
    Intent,
    KnowledgeBot,
    KnowledgeSearchEngine,
    PermissionLevel,
    PermissionManager,
    recognize_intent,
    format_search_results,
)


def _make_article(path, title, tags, summary, score=0.8, collected="2026-09-06"):
    article = {
        "id": path.stem,
        "title": title,
        "source": "github",
        "url": f"https://github.com/x/{title}",
        "collected_at": f"{collected}T10:00:00+00:00",
        "summary": summary,
        "tags": tags,
        "relevance_score": score,
        "category": "framework",
        "key_insight": "k",
    }
    path.write_text(json.dumps(article, ensure_ascii=False), encoding="utf-8")
    return article


@pytest.fixture
def kb_dir(tmp_path):
    _make_article(tmp_path / "a-001.json", "agent-framework-core", ["llm"], "一个框架", 0.7)
    _make_article(tmp_path / "a-002.json", "cool-tool", ["agent"], "agent 智能体教程", 0.9)
    _make_article(tmp_path / "a-003.json", "rag-engine", ["rag"], "检索增强", 0.85, collected="2026-09-09")
    return tmp_path


class TestRecognizeIntent:
    @pytest.mark.parametrize(
        "text,intent,args",
        [
            ("/search MCP", Intent.SEARCH, "MCP"),
            ("/today", Intent.BROWSE_TODAY, ""),
            ("/top", Intent.BROWSE_TOP, ""),
            ("/top 3", Intent.BROWSE_TOP, "3"),
            ("/help", Intent.HELP, ""),
            ("搜索 Agent 文章", Intent.SEARCH, "Agent 文章"),
            ("搜一下 RAG", Intent.SEARCH, "RAG"),
            ("今天有什么新内容", Intent.BROWSE_TODAY, "有什么新内容"),
            ("推荐几个", Intent.BROWSE_TOP, "几个"),
            ("随便聊聊", Intent.UNKNOWN, "随便聊聊"),
        ],
    )
    def test_cases(self, text, intent, args):
        got_intent, got_args = recognize_intent(text)
        assert got_intent == intent
        assert got_args == args


class TestSearchEngine:
    def test_title_beats_tag_beats_summary(self, kb_dir):
        engine = KnowledgeSearchEngine(kb_dir)
        results = engine.search(keyword="agent", limit=3)
        titles = [r["title"] for r in results]
        # 标题命中(+10) > 标签命中(+5) > 仅摘要命中(+3)
        assert titles[0] == "agent-framework-core"
        assert titles[1] == "cool-tool"
        assert "rag-engine" not in titles

    def test_tag_filter(self, kb_dir):
        engine = KnowledgeSearchEngine(kb_dir)
        results = engine.search(tags=["rag"])
        assert [r["title"] for r in results] == ["rag-engine"]

    def test_date_from(self, kb_dir):
        engine = KnowledgeSearchEngine(kb_dir)
        results = engine.search(date_from="2026-09-09")
        assert [r["title"] for r in results] == ["rag-engine"]

    def test_empty_keyword_browses_by_relevance(self, kb_dir):
        engine = KnowledgeSearchEngine(kb_dir)
        results = engine.search(limit=2)
        assert results[0]["relevance_score"] >= results[1]["relevance_score"]


class TestPermissions:
    def test_default_read_only(self):
        pm = PermissionManager()
        assert pm.has_permission("u1", PermissionLevel.READ)
        assert not pm.has_permission("u1", PermissionLevel.WRITE)
        assert not pm.has_permission("u1", PermissionLevel.DELETE)

    def test_admin_has_all(self):
        pm = PermissionManager(admins={"boss"})
        for level in PermissionLevel:
            assert pm.has_permission("boss", level)


class TestKnowledgeBot:
    @pytest.fixture
    def bot(self, kb_dir, tmp_path):
        return KnowledgeBot(
            knowledge_dir=kb_dir,
            data_dir=tmp_path / "data",
            admins={"vip"},
        )

    def test_search_flow(self, bot):
        reply = bot.handle_message("u1", "/search agent")
        assert "agent-framework-core" in reply and "🔍" in reply

    def test_subscribe_requires_write(self, bot):
        reply = bot.handle_message("u1", "/subscribe RAG")
        assert "WRITE" in reply

    def test_subscribe_admin_ok_and_idempotent(self, bot):
        first = bot.handle_message("vip", "/subscribe RAG")
        second = bot.handle_message("vip", "/subscribe RAG")
        assert "已订阅" in first
        assert "已订阅过" in second
        assert bot.handle_message("vip", "/subscribe").startswith("当前订阅")

    def test_top_with_n(self, bot):
        reply = bot.handle_message("u1", "/top 2")
        assert "top 2" in reply and reply.count("\n") <= 4

    def test_help_and_unknown(self, bot):
        assert "/search" in bot.handle_message("u1", "/help")
        assert "没太明白" in bot.handle_message("u1", "你好呀")


class TestFormatResults:
    def test_empty(self):
        assert "没找到" in format_search_results([], query="x")

    def test_format(self):
        text = format_search_results(
            [{"title": "t", "relevance_score": 0.9, "source": "github",
              "collected_at": "2026-09-06T10:00:00+00:00", "tags": ["a"]}],
            query="t",
        )
        assert "📌 1. t" in text and "2026-09-06" in text
