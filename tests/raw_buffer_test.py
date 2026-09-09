"""三步分时管线测试 — raw 缓冲落盘 / 恢复 / 分模式图拓扑。"""

import json
from unittest.mock import patch

import pytest

from workflows.collector import _save_raw_snapshot, load_raw_node
from workflows.graph import build_graph


@pytest.fixture
def kb_root(tmp_path, monkeypatch):
    """把项目的 knowledge/ 目录重定向到临时目录。"""
    raw_dir = tmp_path / "knowledge" / "raw"
    raw_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "workflows.collector.os.path.dirname",
        lambda p: str(tmp_path / "workflows") if p.endswith("collector.py") else _real_dirname(p),
    )
    return tmp_path, raw_dir


_real_dirname = __import__("os").path.dirname


class TestRawSnapshot:
    def test_save_and_merge_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        base = tmp_path / "knowledge" / "raw"

        # 直接构造 raw 目录结构（绕过 __file__ 定位，用 chdir + monkeypatch os.path）
        import workflows.collector as collector
        fake_root = tmp_path
        monkeypatch.setattr(
            collector.os.path, "abspath", lambda p: str(fake_root / "workflows" / "collector.py")
        )

        items_a = [{"url": "https://x/1", "title": "a"}, {"url": "https://x/2", "title": "b"}]
        _save_raw_snapshot(items_a)
        files = list(base.glob("github-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert len(data) == 2

        # 同日重跑：旧条目保留 + 按 URL 去重
        items_b = [{"url": "https://x/2", "title": "b-v2"}, {"url": "https://x/3", "title": "c"}]
        _save_raw_snapshot(items_b)
        data2 = json.loads(files[0].read_text(encoding="utf-8"))
        assert {d["title"] for d in data2} == {"a", "b-v2", "c"}

    def test_load_raw_node_reads_file(self, tmp_path, monkeypatch):
        import workflows.collector as collector
        monkeypatch.setattr(
            collector.os.path, "abspath", lambda p: str(tmp_path / "workflows" / "collector.py")
        )
        raw_dir = tmp_path / "knowledge" / "raw"
        raw_dir.mkdir(parents=True)
        from datetime import datetime, timezone

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (raw_dir / f"github-{date_str}.json").write_text(
            json.dumps([{"url": "https://x/1", "title": "a"}]), encoding="utf-8"
        )
        out = load_raw_node({})
        assert len(out["sources"]) == 1 and out["sources"][0]["title"] == "a"

    def test_load_raw_node_missing_file(self, tmp_path, monkeypatch):
        import workflows.collector as collector
        monkeypatch.setattr(
            collector.os.path, "abspath", lambda p: str(tmp_path / "workflows" / "collector.py")
        )
        out = load_raw_node({})
        assert out["sources"] == []


class TestGraphModes:
    def test_full_mode_compiles(self):
        app = build_graph("full").compile()
        assert "collect" in app.get_graph().nodes

    def test_collect_mode_compiles(self):
        app = build_graph("collect").compile()
        nodes = app.get_graph().nodes
        assert "collect" in nodes and "analyze" not in nodes

    def test_analyze_mode_compiles(self):
        app = build_graph("analyze").compile()
        nodes = app.get_graph().nodes
        assert "load_raw" in nodes and "collect" not in nodes
        assert "review" in nodes and "organize" in nodes

    def test_default_is_full(self):
        app_full = build_graph().compile()
        app_named = build_graph("full").compile()
        assert set(app_full.get_graph().nodes) == set(app_named.get_graph().nodes)
