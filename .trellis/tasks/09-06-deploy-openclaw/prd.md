# PRD — 部署 AI 知识库 V3 到 Mac mini OpenClaw

## 背景

将本地 `v3-multi-agent` 项目部署到局域网 Mac mini 的 OpenClaw 上，作为常驻知识采集服务，并通过飞书机器人交互。

## 已核实的环境事实（2026-09-06 调研）

| 项 | 值 |
|---|---|
| 目标机 | `wangyao@192.168.124.4`（MacMiniM2-2.local，arm64）|
| SSH | 免密已配（BatchMode 验证通过，无需密码）|
| OpenClaw 二进制 | `/opt/homebrew/bin/openclaw` → `/opt/homebrew/lib/node_modules/openclaw/openclaw.mjs` |
| OpenClaw 数据目录 | `~/.openclaw/`（openclaw.json / agents / workspace / feishu）|
| Gateway 服务 | launchd `ai.openclaw.gateway`，端口 18789，loopback |
| 现有 Agent | 仅 `main` 一个（文献查询 + 豆包图片下载共用，无隔离）|
| 现有飞书 | `channels.feishu.accounts.default`（appId `cli_a94342660879dcb2`）|
| 远端 Python | 仅系统 3.9.6（项目需 3.11+，需 brew 安装）|
| 本机 LLM | `.env` 已配 MiniMax 大陆 Coding Plan：`https://api.minimaxi.com/v1` + `MiniMax-M3`（已实测连通，`<think>` 剥离已修复于 workflows/model_client.py）|

## 隔离方案（已与用户确认：方案 A + 新 Agent）

- **工作目录隔离**：`openclaw agents add kb3`，独立 workspace / agentDir / sessions；workspace 指向 `~/ai-knowledge-base`
- **通讯隔离**：飞书开放平台新建（或重绑闲置）自建应用 → `channels.feishu.accounts.kb3` → binding 到 kb3；旧机器人继续服务 main
- 路由优先级：peer > account > channel > 默认 agent

## 子任务（执行顺序）

1. `09-06-remote-python` — 远端 brew install python@3.12
2. `09-06-sync-code` — rsync 代码（排除 .git/.venv）+ 单独 scp .env
3. `09-06-remote-venv` — venv + pip install + `python -m workflows.graph` 验证
4. `09-06-openclaw-agent` — `openclaw agents add kb3` + workspace 指向项目目录
5. `09-06-feishu-bind` — 用户提供新 appId/appSecret，配置 account + binding
6. `09-06-e2e-verify` — `agents list --bindings` / `channels status --probe` / 飞书端到端触发

## 验收标准

- [ ] Mac mini 上 `.venv/bin/python -m workflows.graph` 全流程跑通，产物落 `knowledge/articles/`
- [ ] `openclaw agents list --bindings` 显示 kb3 且 binding 正确
- [ ] 新飞书机器人可对话触发工作流；main agent 的两个旧项目不受影响
- [ ] `.env` 不进入任何 git 提交

## 安全注意

- `.env` 含 LLM_API_KEY，rsync 后确认权限 600，不入 git（.gitignore 已含）
- Mac mini 上的 openclaw.json 修改前先备份（现有 *.bak 惯例）

## 状态

**等待用户指示后开始执行。**
