"""distribution/formatter.py — 知识条目 → 多渠道消息格式。

纯格式化模块：不做任何网络请求（网络投递属于未来的 publisher.py）。
输入是 v3 LangGraph Organizer 节点产出的单篇文章 JSON，包含
id/title/source/url/collected_at/summary/tags/relevance_score/category/key_insight 十个字段。

支持的输出渠道：
    - Markdown（日常简报 / 仓库 README）
    - Telegram MarkdownV2（需转义特殊字符）
    - 飞书 interactive 卡片（header 按 relevance_score 染色）
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Telegram MarkdownV2 中必须转义的字符（不含链接语法自身的 [] 与 ()）
_TELEGRAM_ESCAPE_CHARS = "_*~`>#+-=|{}.!"

# 相关性评分阈值与 emoji / 飞书模板色的映射
_SCORE_EMOJI_GREEN = "🟢"
_SCORE_EMOJI_YELLOW = "🟟"
_SCORE_EMOJI_RED = "🔴"


def _score_emoji(score: float) -> str:
    """按相关性评分返回等级 emoji。

    Args:
        score: 相关性评分，0.0 ~ 1.0。

    Returns:
        >= 0.8 返回 🟢；>= 0.6 返回 🟟；否则返回 🔴。
    """
    if score >= 0.8:
        return _SCORE_EMOJI_GREEN
    if score >= 0.6:
        return _SCORE_EMOJI_YELLOW
    return _SCORE_EMOJI_RED


def _score_template(score: float) -> str:
    """按相关性评分返回飞书卡片 header 模板色名。

    Args:
        score: 相关性评分，0.0 ~ 1.0。

    Returns:
        "green" / "yellow" / "red" 之一。
    """
    if score >= 0.8:
        return "green"
    if score >= 0.6:
        return "yellow"
    return "red"


def _escape_telegram(text: str) -> str:
    """转义 Telegram MarkdownV2 特殊字符。

    Args:
        text: 原始文本。

    Returns:
        在 _*~`>#+-=|{}.! 每个字符前插入反斜杠后的安全文本。
    """
    escaped: list[str] = []
    for ch in text:
        if ch in _TELEGRAM_ESCAPE_CHARS:
            escaped.append("\\" + ch)
        else:
            escaped.append(ch)
    return "".join(escaped)


def _tag_to_hashtag(tag: str) -> str:
    """把单个标签转换为 Telegram 可用的 hashtag 文本。

    空格替换为下划线，# 号按 MarkdownV2 规则转义。

    Args:
        tag: 原始标签。

    Returns:
        形如 "\\#智能体_工作流" 的文本。
    """
    return "\\" + "#" + tag.replace(" ", "_")


def json_to_markdown(article: dict[str, Any]) -> str:
    """单篇文章 → Markdown 片段。

    Args:
        article: Organizer 产出的文章 JSON。

    Returns:
        包含标题、来源、日期、相关性、标签、摘要、原文链接的 Markdown 文本。
    """
    score = float(article.get("relevance_score", 0.0))
    tags = "、".join(article.get("tags", []))
    lines = [
        f"## {article.get('title', '')}",
        "",
        f"- **来源**: {article.get('source', '')}"
        f" | **日期**: {str(article.get('collected_at', ''))[:10]}"
        f" | **相关性**: {_score_emoji(score)} {score}",
        f"- **标签**: {tags}",
        "",
        article.get("summary", ""),
        "",
        f"[原文链接]({article.get('url', '')})",
    ]
    return "\n".join(lines)


def json_to_telegram(article: dict[str, Any]) -> str:
    """单篇文章 → Telegram MarkdownV2 文本。

    Args:
        article: Organizer 产出的文章 JSON。

    Returns:
        MarkdownV2 文本：标题链接、summary、相关性、来源、hashtag 标签。
        正文字符按 _*~`>#+-=|{}.! 转义，链接 URL 保持原样。
    """
    score = float(article.get("relevance_score", 0.0))
    title = _escape_telegram(article.get("title", ""))
    url = article.get("url", "")
    summary = _escape_telegram(article.get("summary", ""))
    source = _escape_telegram(article.get("source", ""))
    hashtags = " ".join(
        _tag_to_hashtag(t) for t in article.get("tags", [])
    )
    return (
        f"[{title}]({url})\n\n"
        f"{summary}\n\n"
        f"{_score_emoji(score)} 相关性: {score} | 来源: {source}\n\n"
        f"{hashtags}"
    )


def json_to_feishu(article: dict[str, Any]) -> dict[str, Any]:
    """单篇文章 → 飞书 interactive 卡片消息体。

    Args:
        article: Organizer 产出的文章 JSON。

    Returns:
        可直接提交飞书 webhook 的 dict：
        msg_type=interactive，header.template 按 score 染色
        （>=0.8 green / >=0.6 yellow / 否则 red）。
    """
    score = float(article.get("relevance_score", 0.0))
    title = article.get("title", "")
    summary = article.get("summary", "")
    source = article.get("source", "")
    url = article.get("url", "")
    tags = " ".join(f"#{t.replace(' ', '_')}" for t in article.get("tags", []))

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "template": _score_template(score),
                "title": {
                    "tag": "plain_text",
                    "content": f"{_score_emoji(score)} {title}",
                },
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": summary},
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"相关性: **{score}** · 来源: **{source}** · {tags}",
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "原文链接",
                            },
                            "url": url,
                            "type": "primary",
                        }
                    ],
                },
            ],
        },
    }


def generate_daily_digest(
    knowledge_dir: str | Path = "knowledge/articles",
    date: str | None = None,
    top_n: int = 5,
) -> dict[str, Any] | str:
    """生成当日知识简报（按相关性取 Top N）。

    Args:
        knowledge_dir: 知识条目目录，默认 "knowledge/articles"（相对 CWD）。
        date: 日期字符串 YYYY-MM-DD；None 时取 UTC 今天
            （与 Organizer 生成文件 ID 使用的 UTC 日期对齐）。
        top_n: 取相关性最高的前 N 条。

    Returns:
        dict 形如 {"markdown": str, "telegram": str, "feishu": list[dict]}；
        当日无文章时返回字符串 "📭 {date} 暂无新增知识条目"。
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    articles_dir = Path(knowledge_dir)
    articles: list[dict[str, Any]] = []
    for path in sorted(articles_dir.glob(f"{date}-*.json")):
        with open(path, encoding="utf-8") as f:
            articles.append(json.load(f))

    if not articles:
        return f"📭 {date} 暂无新增知识条目"

    articles.sort(key=lambda a: float(a.get("relevance_score", 0.0)), reverse=True)
    top = articles[:top_n]

    header_md = f"# 📚 {date} 知识日报（Top {len(top)}）"
    header_tg = f"*{_escape_telegram(f'{date} 知识日报（Top {len(top)}）')}*"

    return {
        "markdown": "\n\n".join([header_md] + [json_to_markdown(a) for a in top]),
        "telegram": "\n\n———\n\n".join(
            [header_tg] + [json_to_telegram(a) for a in top]
        ),
        "feishu": [json_to_feishu(a) for a in top],
    }
