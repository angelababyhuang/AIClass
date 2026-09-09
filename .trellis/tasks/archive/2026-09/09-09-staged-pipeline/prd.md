# 三步分时管线（raw 缓冲 + 8/9/10 cron）

## Goal

方案B：collector 写 knowledge/raw/（落实 AGENTS.md 规范）+ load_raw 节点 + graph --mode collect/analyze/full + cron 重排 08采/09析/10推 + digest jsonl 推送日志

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
