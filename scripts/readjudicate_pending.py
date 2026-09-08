#!/usr/bin/env python3
"""重新审理 pending_review 积压 —— 平反被旧 Reviewer 误杀的条目。

背景：2026-09-06 ~ 09-08 期间旧 Reviewer 因 LLM 知识过期，把 star 数真实
（已与 GitHub 实时 API 对账吻合）的条目误判为"数据造假"全部打回。修复后的
Reviewer 已回归正轨，本脚本对积压做一次性重审。

流程：
    1. 对账：现有 articles/ 已覆盖的 URL 直接判"已被新批次取代"
    2. 去重：多个 pending 文件中的同一 URL 保留最新 collected_at 的分析
    3. 重审：候选条目按 5 条一批走修复后的 review_node
    4. 平反：过审条目按其原始采集日期编号入库（不覆盖现有文件）
    5. 归档：所有处理过的 pending 文件移入 adjudicated/，产出审理报告

安全设计：URL 排除 + 编号碰撞检测，重复运行幂等。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from workflows.reviewer import review_node  # noqa: E402

PENDING_DIR = ROOT / "knowledge" / "pending_review"
ADJUDICATED_DIR = PENDING_DIR / "adjudicated"
ARTICLES_DIR = ROOT / "knowledge" / "articles"

ARTICLE_FIELDS = (
    "id", "title", "source", "url", "collected_at",
    "summary", "tags", "relevance_score", "category", "key_insight",
)


def load_existing_urls() -> set[str]:
    """读现有 articles/*.json 的 URL 集合（排除 index.json）。"""
    urls: set[str] = set()
    for p in ARTICLES_DIR.glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            urls.add(json.loads(p.read_text(encoding="utf-8"))["url"])
        except (json.JSONDecodeError, KeyError):
            continue
    return urls


def next_id_for(date_str: str) -> str:
    """给定日期生成不冲突的下一个 {date}-{NNN} 编号。"""
    used = {
        p.stem for p in ARTICLES_DIR.glob(f"{date_str}-*.json")
    }
    n = 0
    while f"{date_str}-{n:03d}" in used:
        n += 1
    return f"{date_str}-{n:03d}"


def promote(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把过审条目写入 articles/ 并追加 index.json。"""
    index_path = ARTICLES_DIR / "index.json"
    index: list[dict] = []
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))

    promoted = []
    for item in items:
        date_str = str(item.get("collected_at", ""))[:10] or datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")
        article = {k: item.get(k, "" if k != "relevance_score" else 0.5)
                   for k in ARTICLE_FIELDS}
        article["id"] = next_id_for(date_str)
        article["relevance_score"] = float(article["relevance_score"] or 0.5)
        article["tags"] = item.get("tags", [])

        (ARTICLES_DIR / f"{article['id']}.json").write_text(
            json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        index.append({
            "id": article["id"], "title": article["title"],
            "category": article.get("category", "other"),
            "relevance_score": article["relevance_score"],
        })
        promoted.append(article)
        print(f"  ✅ 平反入库: {article['id']}  {article['title']}")

    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return promoted


def main() -> None:
    ADJUDICATED_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "adjudicated_at": datetime.now(timezone.utc).isoformat(),
        "files": [], "superseded": [], "promoted": [], "still_pending": [],
    }

    existing_urls = load_existing_urls()
    print(f"[对账] 现有知识库已覆盖 URL: {len(existing_urls)} 个")

    # ---- 1. 汇总各 pending 文件，同 URL 保留最新分析 ----
    files = sorted(PENDING_DIR.glob("pending-*.json"))
    best_by_url: dict[str, dict[str, Any]] = {}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for item in data.get("analyses", []):
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                report["files"].append({"file": f.name, "note": "无效条目/测试数据"})
                continue
            prev = best_by_url.get(url)
            if prev is None or str(item.get("collected_at", "")) > str(
                prev.get("collected_at", "")
            ):
                best_by_url[url] = item

    # ---- 2. 拆分：已被新批次取代 vs 待重审 ----
    candidates = []
    for url, item in best_by_url.items():
        if url in existing_urls:
            report["superseded"].append(item.get("title", url))
        else:
            candidates.append(item)
    print(f"[去重] 唯一 URL {len(best_by_url)} 个："
          f"已被取代 {len(report['superseded'])}，待重审 {len(candidates)}")

    # ---- 3. 分批重审（沿用生产的 5 条采样口径）----
    for i in range(0, len(candidates), 5):
        batch = candidates[i : i + 5]
        state = {"analyses": batch, "iteration": 0, "cost_tracker": {}}
        verdict = review_node(state)
        if verdict.get("review_passed"):
            report["promoted"].extend(
                a["id"] for a in promote(batch)
            )
        else:
            report["still_pending"].extend(a.get("title", "?") for a in batch)
            print(f"  ❌ 批次未过审（保留 pending）: {verdict.get('review_feedback', '')[:80]}")

    # ---- 4. 归档所有 pending 文件 ----
    for f in files:
        shutil.move(str(f), ADJUDICATED_DIR / f.name)
    report["files"] = [f.name for f in files]

    (ADJUDICATED_DIR / "adjudication_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[结果] 平反入库 {len(report['promoted'])} 篇 | "
          f"已被新批次取代 {len(report['superseded'])} 篇 | "
          f"仍未过审 {len(report['still_pending'])} 篇")
    print(f"[归档] {len(files)} 份 pending 文件 → knowledge/pending_review/adjudicated/")


if __name__ == "__main__":
    main()
