# distribution/publisher.py 推送模块

## Goal

OOP: BasePublisher 抽象基类(send_message/send_digest) + TelegramPublisher(aiohttp异步,MarkdownV2,30s超时,4096分片) + FeishuPublisher(webhook卡片) + PublishResult 数据类 + publish_daily_digest 并发入口。用户主用飞书

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
