"""
Collector Agent — 数据采集节点

职责：按 state["plan"]["per_source_limit"] 从 GitHub API 抓取热门 AI 项目。

核心原则：只采集不分析（Collect, don't analyze）。
Collector 返回的是原始字段（title/url/description/stars），不做任何 LLM 调用。
"""

import json
import os
from datetime import datetime, timezone

from workflows.state import KBState


def collect_node(state: KBState) -> dict:
    """采集节点：调用 GitHub Trending API 获取今日热门项目

    实际生产中会并行调用多个数据源（GitHub、HN、arXiv）。
    这里以 GitHub 为例，展示数据采集的标准模式。

    读取 state["plan"]["per_source_limit"] 决定抓取条数（由 Planner 节点给出）。
    """
    import urllib.parse
    import urllib.request

    sources: list[dict] = []
    plan = state.get("plan", {}) or {}
    per_source_limit = int(plan.get("per_source_limit", 10))

    github_token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    # 搜索最近一周更新的、星标数高的 AI 相关仓库
    one_week_ago = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(days=7)
    ).strftime("%Y-%m-%d")
    query = f"ai agent llm stars:>100 pushed:>{one_week_ago}"
    url = (
        f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}"
        f"&sort=stars&per_page={per_source_limit}"
    )

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for repo in data.get("items", []):
            sources.append({
                "source": "github",
                "title": repo["full_name"],
                "url": repo["html_url"],
                "description": repo.get("description", ""),
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language", ""),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        # 网络失败时不中断流程，记录错误继续
        sources.append({
            "source": "github",
            "title": "[ERROR] GitHub API 请求失败",
            "url": "",
            "description": str(e),
            "stars": 0,
            "language": "",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"[Collector] 采集到 {len(sources)} 条原始数据")

    # 落盘 raw 快照（AGENTS.md 规范：knowledge/raw/{source}-{YYYY-MM-DD}.json）
    # 文件系统 = 缓冲：采集与分析解耦，分析阶段可独立重跑，不重复消耗 API
    _save_raw_snapshot(sources)

    return {"sources": sources}


def _save_raw_snapshot(sources: list[dict]) -> None:
    """把本次采集合并写入当日 raw 快照（同日重跑按 URL 幂等去重）。

    Args:
        sources: 本次采集的原始条目。
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir = os.path.join(base_dir, "knowledge", "raw")
    os.makedirs(raw_dir, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_path = os.path.join(raw_dir, f"github-{date_str}.json")

    by_url: dict[str, dict] = {}
    if os.path.exists(raw_path):
        try:
            with open(raw_path, encoding="utf-8") as f:
                for item in json.load(f):
                    if item.get("url"):
                        by_url[item["url"]] = item
        except (json.JSONDecodeError, OSError):
            pass
    for item in sources:
        if item.get("url"):
            by_url[item["url"]] = item

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(list(by_url.values()), f, ensure_ascii=False, indent=2)
    print(f"[Collector] raw 快照 → {raw_path}（{len(by_url)} 条，URL 去重）")


def load_raw_node(state: dict) -> dict:
    """从当日 raw 快照加载 sources（分析阶段的图入口节点）。

    日期优先取环境变量 RAW_DATE，默认 UTC 今天；文件缺失时返回空列表
    并告警（下游 Analyzer 对空 sources 会自然跳过）。
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    date_str = os.getenv("RAW_DATE") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )
    raw_path = os.path.join(base_dir, "knowledge", "raw", f"github-{date_str}.json")

    if not os.path.exists(raw_path):
        print(f"[LoadRaw] ⚠️ 未找到 {raw_path}，sources 为空")
        return {"sources": []}

    with open(raw_path, encoding="utf-8") as f:
        sources = json.load(f)
    print(f"[LoadRaw] 从 {raw_path} 加载 {len(sources)} 条原始数据")
    return {"sources": sources}
