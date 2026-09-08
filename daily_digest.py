#!/usr/bin/env python3
"""AI 知识库 — 每日摘要推送入口。

串联 distribution/formatter.py 与 distribution/publisher.py：
    knowledge/articles/ 采集产物 → generate_daily_digest → 并发推送飞书 / Telegram。

用法:
    .venv/bin/python daily_digest.py                # 推送 UTC 今天
    .venv/bin/python daily_digest.py --yesterday    # 推送 UTC 昨天（早间 cron 推荐）
    .venv/bin/python daily_digest.py --date 2026-09-08
    .venv/bin/python daily_digest.py --force        # 忽略幂等标记强制重推

幂等性：同一日期成功推送过（logs/last_digest_date）后自动跳过，防止 cron
重复触发导致重复推送；--force 可覆盖。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from distribution.publisher import publish_daily_digest

ROOT = Path(__file__).resolve().parent
MARKER_PATH = ROOT / "logs" / "last_digest_date"

BANNER = (
    "==================================================\n"
    "  AI 知识库 — 每日摘要推送\n"
    "=================================================="
)


def resolve_date(args: argparse.Namespace) -> str:
    """根据命令行参数解析目标日期。

    Args:
        args: 已解析的命令行参数。

    Returns:
        YYYY-MM-DD 格式日期字符串。
    """
    if args.date:
        return args.date
    if args.yesterday:
        return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 知识库每日摘要推送")
    parser.add_argument("--date", help="推送日期 YYYY-MM-DD（默认 UTC 今天）")
    parser.add_argument(
        "--yesterday",
        action="store_true",
        help="推送 UTC 昨天（北京早晨 8 点 = UTC 0 点，正好覆盖前一 UTC 日的采集）",
    )
    parser.add_argument("--top-n", type=int, default=5, help="简报最多条数")
    parser.add_argument("--force", action="store_true", help="忽略幂等标记强制重推")
    args = parser.parse_args()

    print(BANNER)
    date = resolve_date(args)

    if (
        not args.force
        and MARKER_PATH.exists()
        and MARKER_PATH.read_text(encoding="utf-8").strip() == date
    ):
        print(f"[INFO] [幂等] {date} 已推送过，跳过（--force 可强制重推）")
        return 0

    print(f"[INFO] [生成] {date} 日报（Top {args.top_n}）")
    results = asyncio.run(
        publish_daily_digest(
            knowledge_dir=ROOT / "knowledge" / "articles",
            date=date,
            top_n=args.top_n,
        )
    )

    ok_count = sum(1 for r in results if r.success)
    print(f"\n[INFO] [发布] 完成：{ok_count}/{len(results)} 个渠道成功\n")
    print(f"推送结果: {ok_count}/{len(results)} 个渠道成功")
    for r in results:
        if r.success:
            extra = f": {r.message_id}" if r.message_id else ""
            print(f"  ✅ {r.channel}{extra}")
        else:
            print(f"  ❌ {r.channel}: {r.error}")

    if ok_count:
        MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARKER_PATH.write_text(date, encoding="utf-8")

    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
