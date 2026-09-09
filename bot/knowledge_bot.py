"""bot/knowledge_bot.py — 交互式 AI 知识库机器人。

包含：规则式意图识别、加权知识检索、订阅管理、三级权限控制。
OOP 架构，可对接 Telegram / 飞书 / 命令行等前端。

加权检索算法：标题命中 +10、标签命中 +5、摘要命中 +3；
命中原分相同时按 relevance_score 排序。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

WEIGHT_TITLE = 10
WEIGHT_TAG = 5
WEIGHT_SUMMARY = 3


class Intent(Enum):
    """用户消息的意图类型。"""

    SEARCH = "search"
    BROWSE_TODAY = "browse_today"
    BROWSE_TOP = "browse_top"
    SUBSCRIBE = "subscribe"
    HELP = "help"
    UNKNOWN = "unknown"


COMMAND_MAP: dict[str, Intent] = {
    "/search": Intent.SEARCH,
    "/today": Intent.BROWSE_TODAY,
    "/top": Intent.BROWSE_TOP,
    "/subscribe": Intent.SUBSCRIBE,
    "/help": Intent.HELP,
}

# 自然语言关键词 → 意图（命令前缀匹配不中时按顺序尝试）
KEYWORD_RULES: list[tuple[str, Intent]] = [
    (r"搜索|搜一下|查询|查一下|找(一)?下|找几", Intent.SEARCH),
    (r"今天|今日|简报|最新", Intent.BROWSE_TODAY),
    (r"top|排行|最高|最佳|推荐", Intent.BROWSE_TOP),
    (r"订阅|subscribe", Intent.SUBSCRIBE),
    (r"帮助|指令|怎么用|help", Intent.HELP),
]

# 意图提取参数时需要剥掉的引导词
_LEAD_WORDS = re.compile(
    r"^(请|帮我|我要|我想)?(搜索|搜一下|查询|查一下|找一下|找下|看下|看看|订阅|今天|今日|最新|推荐|排行)\s*"
)


class PermissionLevel(Enum):
    """三级权限：只读 / 读写 / 删除。"""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"


_PERMISSION_LADDER = {
    PermissionLevel.READ: 1,
    PermissionLevel.WRITE: 2,
    PermissionLevel.DELETE: 3,
}


class PermissionManager:
    """三级权限控制。

    未登记的用户默认只有 READ 权限；管理员拥有全部权限。

    Attributes:
        user_permissions: 用户 ID → 已授予权限集合。
    """

    def __init__(
        self,
        user_permissions: dict[str, set[PermissionLevel]] | None = None,
        admins: set[str] | None = None,
    ) -> None:
        self.user_permissions: dict[str, set[PermissionLevel]] = {
            u: set(p) for u, p in (user_permissions or {}).items()
        }
        for admin in admins or set():
            self.user_permissions[admin] = set(PermissionLevel)

    def has_permission(self, user_id: str, level: PermissionLevel) -> bool:
        """检查用户是否具备指定权限（高级权限隐含低级权限）。

        Args:
            user_id: 用户标识。
            level: 要求的权限级别。

        Returns:
            是否具备权限。
        """
        granted = self.user_permissions.get(user_id, {PermissionLevel.READ})
        return any(
            _PERMISSION_LADDER[g] >= _PERMISSION_LADDER[level] for g in granted
        )


class KnowledgeSearchEngine:
    """知识库检索引擎，基于本地 JSON 文件做加权匹配。"""

    def __init__(self, knowledge_dir: str | Path = "knowledge/articles") -> None:
        self.knowledge_dir = Path(knowledge_dir)

    def _load_articles(self) -> list[dict[str, Any]]:
        """读取全部知识条目（跳过 index.json）。"""
        articles: list[dict[str, Any]] = []
        for path in sorted(self.knowledge_dir.glob("*.json")):
            if path.name == "index.json":
                continue
            try:
                articles.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return articles

    @staticmethod
    def _match_score(article: dict[str, Any], keyword: str) -> int:
        """计算单篇文章对关键词的加权命中分。

        Args:
            article: 知识条目。
            keyword: 小写化的查询词；空串视为 0 分。

        Returns:
            标题 +10 / 标签 +5 / 摘要 +3 的累加值。
        """
        if not keyword:
            return 0
        score = 0
        if keyword in str(article.get("title", "")).lower():
            score += WEIGHT_TITLE
        if any(keyword in str(t).lower() for t in article.get("tags", [])):
            score += WEIGHT_TAG
        if keyword in str(article.get("summary", "")).lower():
            score += WEIGHT_SUMMARY
        return score

    def search(
        self,
        keyword: str = "",
        tags: list[str] | None = None,
        date_from: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """搜索知识库条目。

        同一标题多次采集只保留最优一条（命中分 > relevance_score >
        采集时间更新），避免跨日期重复刷屏。

        Args:
            keyword: 关键词（标题/标签/摘要加权匹配）。
            tags: 标签过滤（条目需包含任一标签）。
            date_from: 起始日期 YYYY-MM-DD（按 collected_at 前缀比较）。
            limit: 最多返回条数。

        Returns:
            按（命中分, relevance_score）降序的去重条目列表。
        """
        kw = keyword.strip().lower()
        candidates: list[tuple[int, float, str, dict[str, Any]]] = []
        for article in self._load_articles():
            if date_from and not str(article.get("collected_at", "")) >= date_from:
                continue
            if tags and not any(
                t in [str(x).lower() for x in article.get("tags", [])]
                for t in [str(x).lower() for x in tags]
            ):
                continue
            match = self._match_score(article, kw)
            if kw and match == 0:
                continue
            candidates.append(
                (
                    match,
                    float(article.get("relevance_score", 0.0)),
                    str(article.get("collected_at", "")),
                    article,
                )
            )

        best_by_title: dict[str, tuple[int, float, str, dict[str, Any]]] = {}
        for cand in candidates:
            title = cand[3].get("title", "")
            prev = best_by_title.get(title)
            if prev is None or (cand[0], cand[1], cand[2]) > (prev[0], prev[1], prev[2]):
                best_by_title[title] = cand

        ranked = sorted(
            best_by_title.values(), key=lambda x: (x[0], x[1]), reverse=True
        )
        return [c[3] for c in ranked[:limit]]


class SubscriptionManager:
    """用户订阅管理（关键词订阅，持久化到 JSON 文件）。"""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.data_dir / "subscriptions.json"
        self._subscriptions: dict[str, list[str]] = self._load()

    def _load(self) -> dict[str, list[str]]:
        if not self.store_path.exists():
            return {}
        try:
            return json.loads(self.store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        self.store_path.write_text(
            json.dumps(self._subscriptions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def subscribe(self, user_id: str, keyword: str) -> str:
        """为用户添加关键词订阅（幂等）。

        Returns:
            给用户看的确认文本。
        """
        subs = self._subscriptions.setdefault(user_id, [])
        if keyword not in subs:
            subs.append(keyword)
            self._save()
            return f"✅ 已订阅关键词「{keyword}」，日报将为你关注相关条目"
        return f"你已订阅过「{keyword}」啦"

    def unsubscribe(self, user_id: str, keyword: str) -> str:
        """移除用户的一个订阅。

        Returns:
            给用户看的确认文本。
        """
        subs = self._subscriptions.get(user_id, [])
        if keyword in subs:
            subs.remove(keyword)
            self._save()
            return f"已退订「{keyword}」"
        return f"你没有订阅过「{keyword}」"

    def get_subscriptions(self, user_id: str) -> list[str]:
        """返回用户的全部订阅关键词。"""
        return list(self._subscriptions.get(user_id, []))


def recognize_intent(text: str) -> tuple[Intent, str]:
    """识别用户输入的意图和参数。

    规则匹配（不用 LLM）：命令前缀优先，其次自然语言关键词；
    命令前缀的参数取其后剩余文本，自然语言则剥掉引导词。

    Args:
        text: 用户原始输入。

    Returns:
        (Intent, 参数字符串)。
    """
    text = text.strip()
    for cmd, intent in COMMAND_MAP.items():
        if text.lower().startswith(cmd):
            return intent, text[len(cmd):].strip()
    for pattern, intent in KEYWORD_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            args = _LEAD_WORDS.sub("", text).strip()
            return intent, args
    return Intent.UNKNOWN, text


def format_search_results(
    results: list[dict[str, Any]], *, query: str = ""
) -> str:
    """把搜索结果格式化为用户友好的文本。

    Args:
        results: 条目列表。
        query: 原查询词（用于标题展示，可为空）。

    Returns:
        格式化文本；空结果时返回未命中提示。
    """
    if not results:
        return f"🔍 没找到与「{query}」相关的内容，换个关键词试试？"
    lines = [f"🔍 找到 {len(results)} 条与「{query}」相关的内容：", ""]
    for i, a in enumerate(results, 1):
        date = str(a.get("collected_at", ""))[:10]
        tags = ", ".join(a.get("tags", []))
        lines.append(f"📌 {i}. {a.get('title', '')}")
        lines.append(
            f"   📊 {a.get('relevance_score', '?')} | "
            f"{a.get('source', '?')} | {date} | {tags}"
        )
    return "\n".join(lines)


class KnowledgeBot:
    """AI 知识库交互式机器人。

    整合意图识别、检索、订阅、权限四个模块，handle_message 是唯一入口。

    Attributes:
        search_engine: 检索引擎。
        subscription_mgr: 订阅管理器。
        permission_mgr: 权限管理器。
    """

    def __init__(
        self,
        knowledge_dir: str | Path = "knowledge/articles",
        data_dir: str | Path = "data",
        admins: set[str] | None = None,
    ) -> None:
        self.search_engine = KnowledgeSearchEngine(knowledge_dir)
        self.subscription_mgr = SubscriptionManager(data_dir)
        self.permission_mgr = PermissionManager(admins=admins)

    def handle_message(self, user_id: str, text: str) -> str:
        """处理用户消息，返回响应文本。

        Args:
            user_id: 用户标识（渠道侧的唯一 ID）。
            text: 用户原始输入。

        Returns:
            Bot 响应文本。
        """
        intent, args = recognize_intent(text)
        handlers = {
            Intent.SEARCH: self._handle_search,
            Intent.BROWSE_TODAY: self._handle_today,
            Intent.BROWSE_TOP: self._handle_top,
            Intent.SUBSCRIBE: self._handle_subscribe,
            Intent.HELP: self._handle_help,
            Intent.UNKNOWN: self._handle_unknown,
        }
        return handlers.get(intent, self._handle_unknown)(user_id, args)

    def _handle_search(self, user_id: str, query: str) -> str:
        if not self.permission_mgr.has_permission(user_id, PermissionLevel.READ):
            return "⛔ 你没有读取权限"
        results = self.search_engine.search(keyword=query, limit=5)
        return format_search_results(results, query=query)

    def _handle_today(self, user_id: str, args: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        results = self.search_engine.search(date_from=today, limit=5)
        return format_search_results(results, query=f"今天（{today}）新增")

    def _handle_top(self, user_id: str, args: str) -> str:
        n = 5
        if args.strip().isdigit():
            n = max(1, int(args.strip()))
        all_articles = self.search_engine.search(limit=10_000)
        best_by_title: dict[str, dict[str, Any]] = {}
        for a in sorted(
            all_articles,
            key=lambda x: float(x.get("relevance_score", 0)),
            reverse=True,
        ):
            best_by_title.setdefault(a.get("title", ""), a)
        top = list(best_by_title.values())[:n]
        lines = [f"⭐ 高分推荐 top {len(top)}：", ""]
        for i, a in enumerate(top, 1):
            lines.append(
                f"{i}. {a['title']} · score {a.get('relevance_score')} · "
                f"{a.get('category', 'other')}"
            )
        return "\n".join(lines)

    def _handle_subscribe(self, user_id: str, args: str) -> str:
        if not self.permission_mgr.has_permission(user_id, PermissionLevel.WRITE):
            return "⛔ 订阅需要 WRITE 权限，请联系管理员开通"
        keyword = args.strip()
        if not keyword:
            current = self.subscription_mgr.get_subscriptions(user_id)
            listed = "、".join(current) if current else "（暂无）"
            return f"当前订阅：{listed}\n用法：/subscribe 关键词"
        return self.subscription_mgr.subscribe(user_id, keyword)

    def _handle_help(self, user_id: str, args: str) -> str:
        return (
            "🤖 知识库 Bot 指令：\n"
            "  /search 关键词 — 搜索知识库（标题+10 标签+5 摘要+3 加权）\n"
            "  /today — 今日新增\n"
            "  /top [N] — 高分推荐（默认 5）\n"
            "  /subscribe 关键词 — 订阅（需 WRITE 权限）\n"
            "  /help — 本帮助\n"
            "也支持自然语言：搜一下 RAG / 今天有什么新内容 / 推荐几个"
        )

    def _handle_unknown(self, user_id: str, text: str) -> str:
        return "🤔 没太明白，试试 /help 看看我能做什么"


if __name__ == "__main__":
    cli_bot = KnowledgeBot(admins={"cli-user"})
    print("知识库 Bot 已启动（输入 quit 退出）")
    while True:
        try:
            text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() in ("quit", "exit"):
            break
        print(f"助手：{cli_bot.handle_message('cli-user', text)}")
