# Reviewer 反数据误判修复

## Goal

已证实：star 数与实时 API 吻合，Reviewer 因 LLM 知识过期误判数据造假连续打回。修复：① 审核样本投影掉 stars/language ② prompt 加评分纪律（元数据是客观数据，禁止真实性扣分，只按 5 维评分）

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
