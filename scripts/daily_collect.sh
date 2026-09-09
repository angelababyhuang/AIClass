#!/bin/bash
# AI 知识库 — 三步分时管线脚本（由 Mac mini crontab 按时段调用）
#
# 用法:
#   daily_collect.sh collect   # 08:00 仅采集 → 写 knowledge/raw/ → git 回流
#   daily_collect.sh analyze   # 09:00 从 raw/ 分析入库 → git 回流
#   daily_collect.sh full      # 一体化全流程（手动/应急用）
#
# 文件系统 = 缓冲：采集与分析解耦、故障隔离、可独立重跑。

set -u

MODE="${1:-full}"
ROOT="$HOME/ai-knowledge-base"
STAMP=$(date +"%Y-%m-%d %H:%M:%S")

mkdir -p "$ROOT/logs"
cd "$ROOT" || { echo "[$STAMP] FATAL: $ROOT 不存在"; exit 1; }

echo "=== [$STAMP] 管线启动 (mode=$MODE) ==="

# 1. 先同步远端（本机白天可能有代码/文档推送）
if git pull --rebase -q origin main; then
    echo "[$STAMP] 已同步远端"
else
    echo "[$STAMP] ⚠️ git pull 失败，继续用本地状态运行"
fi

# 2. 按模式运行流水线
.venv/bin/python -m workflows.graph --mode "$MODE"
WORKFLOW_EXIT=$?

# 3. 提交产物（raw/ 与 articles/ 都在 knowledge/ 下，统一提交）
if [ -n "$(git status --porcelain knowledge/)" ]; then
    git add knowledge/
    git commit -q -m "chore(staged): $MODE $(date '+%F %R') — 三步分时管线自动运行"
    echo "[$STAMP] 已提交: $(git log --oneline -1)"
    if git push -q origin main; then
        echo "[$STAMP] 已回流 AIClass ✓"
    else
        echo "[$STAMP] ⚠️ 推送失败，产物已在本地提交，下次运行重试"
    fi
else
    echo "[$STAMP] 无新增产物，跳过提交"
fi

echo "=== [$STAMP] 管线结束 (mode=$MODE, workflow exit=$WORKFLOW_EXIT) ==="
