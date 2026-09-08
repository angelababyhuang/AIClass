#!/bin/bash
# AI 知识库 — 定时采集 + Git 回流脚本（由 Mac mini crontab 调用）
#
# 流程：同步远端 → 运行 LangGraph 采集流水线 → 提交 knowledge/ 产物 → 推送 AIClass
# 设计：任一步失败不阻断后续提交（产物落盘优先），推送失败留给下次采集重试。

set -u

ROOT="$HOME/ai-knowledge-base"
STAMP=$(date +"%Y-%m-%d %H:%M:%S")

mkdir -p "$ROOT/logs"
cd "$ROOT" || { echo "[$STAMP] FATAL: $ROOT 不存在"; exit 1; }

echo "=== [$STAMP] 采集开始 ==="

# 1. 先同步远端（本机白天可能有代码/文档推送）
if git pull --rebase -q origin main; then
    echo "[$STAMP] 已同步远端"
else
    echo "[$STAMP] ⚠️ git pull 失败，继续用本地状态采集"
fi

# 2. 采集流水线（GitHub 采集 + MiniMax 分析 + 审核循环 + 入库）
.venv/bin/python -m workflows.graph
WORKFLOW_EXIT=$?

# 3. 提交采集产物（articles / pending_review / index）
if [ -n "$(git status --porcelain knowledge/)" ]; then
    git add knowledge/
    git commit -q -m "chore(daily): 采集 $(date '+%F %R') — workflows.graph 自动运行"
    echo "[$STAMP] 已提交: $(git log --oneline -1)"
    # 4. 推送回流（失败不中断，下次采集前 pull --rebase 会再合并）
    if git push -q origin main; then
        echo "[$STAMP] 已回流 AIClass ✓"
    else
        echo "[$STAMP] ⚠️ 推送失败，产物已在本地提交，下次运行重试"
    fi
else
    echo "[$STAMP] 无新增产物（可能全部未过审或当日已采集），跳过提交"
fi

echo "=== [$STAMP] 采集结束 (workflow exit=$WORKFLOW_EXIT) ==="
