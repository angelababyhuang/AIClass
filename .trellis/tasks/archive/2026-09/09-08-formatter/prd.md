# PRD — distribution/formatter.py

## 需求（课程给定）

输入：单篇文章 JSON（v3 LangGraph Organizer 节点产出，10 字段）。

1. `json_to_markdown(article) -> str`：标题、来源、日期（collected_at 前 10 位）、相关性评分（>=0.8 🟢 / >=0.6 🟟 / 否则 🔴）、标签、summary、原文链接
2. `json_to_telegram(article) -> str`：Telegram MarkdownV2；转义 `_*~\`>#+-=|{}.!`；标题链接、summary、相关性、来源、tag（空格→下划线）
3. `json_to_feishu(article) -> dict`：`msg_type=interactive`，`header.template` 按 score 染色 green/yellow/red
4. `generate_daily_digest(knowledge_dir="knowledge/articles", date=None, top_n=5) -> dict | str`：date=None 取 UTC 今天（与 organizer 文件 ID 的 UTC 日期对齐）；`Path.glob(f"{date}-*.json")`；relevance_score 降序 Top N；返回 `{"markdown", "telegram", "feishu"}`；无文章返回 `"📭 {date} 暂无新增知识条目"`

## 约束

- PEP 8 / Google 风格 docstring / 类型注解
- 纯函数：无网络、无全局状态（网络属于未来的 publisher.py）
- 测试放 `tests/formatter_test.py`（pytest，与现有 tests/ 约定一致）

## 验收标准

- [ ] 四个函数行为符合上述规格
- [ ] MarkdownV2 转义后字符串可通过 Telegram 规则校验（手工核对字符集）
- [ ] 用 `knowledge/articles/2026-09-06-*.json` 真实数据生成日报冒烟通过
- [ ] pytest 全绿
