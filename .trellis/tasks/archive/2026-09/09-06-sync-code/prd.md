# rsync 同步代码与配置

## Goal

rsync -avz --exclude .git --exclude .venv 项目/ 到 wangyao@192.168.124.4:~/ai-knowledge-base/；单独 scp .env（含 MiniMax M3 配置，勿入 git）

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
