"""
工作流节点定义 — 知识库流水线的 6 个核心节点

每个节点是一个纯函数: State → dict（部分状态更新）
LangGraph 会自动将返回值合并到全局 State 中。

节点调用链:
    plan → collect → analyze → organize → review → (conditional) → save
                                    ↑                                │
                                    └── organize (retry) ←───────────┘ (如果审核未通过)

【职责 vs 文件的映射说明（重要教学点）】
- Planner 职责    → patterns/planner.py::planner_node  （只规划）
- Collector 职责  → collect_node                      （只采集）
- Analyzer 职责   → analyze_node                      （只分析单条）
- Organizer 职责  → organize_node + _organize_fresh   （初次整理）
- Reviser 职责    → organize_node + _organize_with_feedback (带反馈修正)
                   ↑ Organizer 和 Reviser 是同一节点的两种模式
- Reviewer 职责   → review_node                       （只评分，不改）
- Saver 职责      → save_node                         （只持久化）
"""

import json
import os
from datetime import datetime, timezone

from patterns.planner import planner_node  # noqa: F401  # re-export for graph.py
from workflows.model_client import accumulate_usage, chat, chat_json
from workflows.state import KBState


# ---------------------------------------------------------------------------
# 节点 1: 采集节点 — 从 GitHub API + RSS 获取原始数据
# ---------------------------------------------------------------------------
def collect_node(state: KBState) -> dict:
    """采集节点：调用 GitHub Trending API 获取今日热门项目

    实际生产中会并行调用多个数据源（GitHub、HN、arXiv）。
    这里以 GitHub 为例，展示数据采集的标准模式。

    读取 state["plan"]["per_source_limit"] 决定抓取条数（由 Planner 节点给出）。
    """
    import urllib.request
    import urllib.parse

    sources: list[dict] = []

    # 读 Planner 策略，没有就用默认 10
    plan = state.get("plan", {}) or {}
    per_source_limit = int(plan.get("per_source_limit", 10))

    # --- GitHub Trending (通过 Search API 近似) ---
    github_token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    # 搜索最近一周更新的、星标数高的 AI 相关仓库
    one_week_ago = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=7)).strftime("%Y-%m-%d")
    query = f"ai agent llm stars:>100 pushed:>{one_week_ago}"
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&sort=stars&per_page={per_source_limit}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for repo in data.get("items", []):
            sources.append({
                "source": "github",
                "title": repo["full_name"],
                "url": repo["html_url"],
                "description": repo.get("description", ""),
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language", ""),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        # 网络失败时不中断流程，记录错误继续
        sources.append({
            "source": "github",
            "title": "[ERROR] GitHub API 请求失败",
            "url": "",
            "description": str(e),
            "stars": 0,
            "language": "",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"[Collector] 采集到 {len(sources)} 条原始数据")
    return {"sources": sources}


# ---------------------------------------------------------------------------
# 节点 2: 分析节点 — 用 LLM 对每条数据进行深度分析
# ---------------------------------------------------------------------------
def analyze_node(state: KBState) -> dict:
    """分析节点：对采集到的原始数据进行 LLM 分析

    为每条数据生成:
    - 中文技术摘要 (200字以内)
    - 相关标签 (英文)
    - 相关性评分 (0.0 - 1.0)
    - 技术领域分类
    """
    sources = state["sources"]
    analyses: list[dict] = []
    tracker = state.get("cost_tracker", {})

    for item in sources:
        # 跳过错误条目
        if item.get("title", "").startswith("[ERROR]"):
            continue

        prompt = f"""请分析以下技术项目/文章，用 JSON 格式返回：

项目名: {item['title']}
描述: {item.get('description', '无描述')}
来源: {item['source']}
URL: {item.get('url', '')}

请返回以下格式的 JSON:
{{
    "summary": "200字以内的中文技术摘要",
    "tags": ["标签1", "标签2", "标签3"],
    "relevance_score": 0.8,
    "category": "分类（如: llm, agent, rag, tool, framework）",
    "key_insight": "一句话核心洞察"
}}"""

        try:
            result, usage = chat_json(prompt)
            tracker = accumulate_usage(tracker, usage)

            analyses.append({
                **item,
                "summary": result.get("summary", ""),
                "tags": result.get("tags", []),
                "relevance_score": result.get("relevance_score", 0.5),
                "category": result.get("category", "other"),
                "key_insight": result.get("key_insight", ""),
            })
        except Exception as e:
            print(f"[Analyzer] 分析失败: {item['title']} - {e}")
            analyses.append({
                **item,
                "summary": f"分析失败: {e}",
                "tags": [],
                "relevance_score": 0.0,
                "category": "error",
                "key_insight": "",
            })

    print(f"[Analyzer] 完成 {len(analyses)} 条分析")
    return {"analyses": analyses, "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# 节点 3: 整理节点 — 双模式：首次整理（Organizer） / 带反馈修正（Reviser）
# ---------------------------------------------------------------------------
# 【教学重点：一个节点，两种职责】
# organize_node 在代码里是一个节点，但承担两个逻辑职责:
#   • 首次进入    → _organize_fresh()        (职责 = Organizer: 过滤+去重+格式化)
#   • 带反馈回流  → _organize_with_feedback() (职责 = Reviser: 读反馈，改条目)
# 这种"一节点两职责"是工程简化 —— 物理文件少，逻辑仍然清晰。
# 在 PPT 里它们是两个独立的 Agent（Organizer / Reviser），在代码里用分支实现。
# ---------------------------------------------------------------------------
def _organize_fresh(analyses: list[dict], plan: dict) -> list[dict]:
    """【Organizer 职责】首次整理：相关性过滤 + URL 去重 + 格式化

    Args:
        analyses: 来自 analyze_node 的分析结果
        plan: Planner 给出的策略，读取其中的 relevance_threshold

    Returns:
        articles: 格式化后的知识条目列表
    """
    threshold = float(plan.get("relevance_threshold", 0.6))

    # 步骤 1: 相关性过滤（阈值由 Planner 决定）
    qualified = [a for a in analyses if a.get("relevance_score", 0) >= threshold]

    # 步骤 2: URL 去重
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in qualified:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    # 步骤 3: 生成标准格式的知识条目
    articles: list[dict] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for i, item in enumerate(unique):
        articles.append({
            "id": f"{today}-{i:03d}",
            "title": item.get("title", ""),
            "source": item.get("source", "unknown"),
            "url": item.get("url", ""),
            "collected_at": item.get("collected_at", ""),
            "summary": item.get("summary", ""),
            "tags": item.get("tags", []),
            "relevance_score": item.get("relevance_score", 0.5),
            "category": item.get("category", "other"),
            "key_insight": item.get("key_insight", ""),
        })
    return articles


def _organize_with_feedback(
    articles: list[dict], feedback: str, tracker: dict
) -> tuple[list[dict], dict]:
    """【Reviser 职责】带反馈修正：读审核反馈，用 LLM 改条目

    Args:
        articles: 上一轮已整理的 articles（已格式化）
        feedback: review_node 给出的具体改进建议
        tracker: 成本追踪器

    Returns:
        (revised_articles, updated_tracker)
    """
    prompt = f"""你是知识库编辑。以下是审核员的反馈，请据此改进这些知识条目。

审核反馈:
{feedback}

当前条目 (JSON):
{json.dumps(articles, ensure_ascii=False, indent=2)}

请返回改进后的条目列表（JSON 数组），保持相同字段结构。"""

    try:
        improved, usage = chat_json(prompt)
        tracker = accumulate_usage(tracker, usage)
        if isinstance(improved, list) and improved:
            return improved, tracker
    except Exception as e:
        print(f"[Reviser] 根据反馈修正失败: {e}，沿用原 articles")

    return articles, tracker


def organize_node(state: KBState) -> dict:
    """整理节点：根据是否有反馈，走 Organizer 或 Reviser 分支

    首次进入（iteration=0，无 feedback）→ _organize_fresh()
    带反馈回流（iteration>0，有 feedback）→ _organize_with_feedback()
    """
    analyses = state["analyses"]
    feedback = state.get("review_feedback", "")
    iteration = state.get("iteration", 0)
    tracker = state.get("cost_tracker", {})
    plan = state.get("plan", {}) or {}

    if feedback and iteration > 0 and state.get("articles"):
        # ---- Reviser 分支：读反馈，改已有 articles ----
        articles, tracker = _organize_with_feedback(
            state["articles"], feedback, tracker
        )
        print(f"[Reviser] 根据反馈修正 {len(articles)} 条条目 (迭代 {iteration})")
    else:
        # ---- Organizer 分支：首次从 analyses 整理 ----
        articles = _organize_fresh(analyses, plan)
        print(f"[Organizer] 整理出 {len(articles)} 条知识条目 (迭代 {iteration})")

    return {"articles": articles, "cost_tracker": tracker}


# ---------------------------------------------------------------------------
# 节点 4: 审核节点 — LLM 质量审核，决定通过或打回
# ---------------------------------------------------------------------------
# 【教学重点】这是 Review Loop 的核心！
# 审核节点评估文章质量，返回 pass/fail + 具体反馈。
# 如果 fail，工作流会回到 organize_node 进行修正，最多循环 3 次。
# ---------------------------------------------------------------------------
def review_node(state: KBState) -> dict:
    """审核节点：对知识条目进行质量审核

    审核维度:
    1. 摘要质量 — 是否准确、简洁、有洞察
    2. 标签准确性 — 是否与内容匹配
    3. 分类合理性 — 是否归入正确类别
    4. 整体一致性 — 条目之间是否有冲突或重复

    Returns:
        review_passed: True/False
        review_feedback: 具体反馈意见
        iteration: 递增的迭代计数器
    """
    articles = state.get("articles", [])
    iteration = state.get("iteration", 0)
    tracker = state.get("cost_tracker", {})
    plan = state.get("plan", {}) or {}
    max_iter = int(plan.get("max_iterations", 3))

    if not articles:
        return {
            "review_passed": True,
            "review_feedback": "没有条目需要审核",
            "iteration": iteration + 1,
        }

    prompt = f"""你是知识库质量审核员。请审核以下知识条目：

{json.dumps(articles, ensure_ascii=False, indent=2)}

请按以下维度评估（每项1-5分）：
1. 摘要质量：准确性、简洁性、洞察深度
2. 标签准确性：标签是否与内容匹配
3. 分类合理性：类别是否正确
4. 整体一致性：条目间是否有冲突或冗余

请用 JSON 格式回复：
{{
    "passed": true或false,
    "overall_score": 4.2,
    "feedback": "具体的改进建议（如果不通过）",
    "scores": {{
        "summary_quality": 4,
        "tag_accuracy": 3,
        "category_correctness": 5,
        "consistency": 4
    }}
}}

评分标准：overall_score >= 3.5 即通过。第 {iteration + 1} 次审核（最多3次）。"""

    try:
        result, usage = chat_json(
            prompt,
            system="你是严格但公正的知识库质量审核员。给出具体、可操作的反馈。",
        )
        tracker = accumulate_usage(tracker, usage)

        passed = result.get("passed", False)
        feedback = result.get("feedback", "")
        score = result.get("overall_score", 0)

        # 达到 Planner 设定的最大迭代次数时强制通过，避免无限循环
        if iteration + 1 >= max_iter:
            passed = True
            feedback += f"\n[系统] 已达最大审核次数({max_iter}次)，强制通过。"

        print(f"[Reviewer] 审核得分: {score}, 通过: {passed} (迭代 {iteration + 1}/{max_iter})")

    except Exception as e:
        # LLM 调用失败时直接通过，不阻塞流程
        passed = True
        feedback = f"审核 LLM 调用失败: {e}，自动通过"
        print(f"[Reviewer] 审核失败，自动通过: {e}")

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration + 1,
        "cost_tracker": tracker,
    }


# ---------------------------------------------------------------------------
# 节点 5: 保存节点 — 写入最终知识条目到文件
# ---------------------------------------------------------------------------
def save_node(state: KBState) -> dict:
    """保存节点：将通过审核的知识条目写入 JSON 文件

    输出路径: knowledge/articles/{date}-{index}.json
    同时更新 knowledge/articles/index.json 索引文件
    """
    articles = state.get("articles", [])
    tracker = state.get("cost_tracker", {})

    if not articles:
        print("[Saver] 没有条目需要保存")
        return state

    # 确定输出目录（相对于项目根目录）
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    articles_dir = os.path.join(base_dir, "knowledge", "articles")
    os.makedirs(articles_dir, exist_ok=True)

    # 保存每篇文章
    saved_files: list[str] = []
    for article in articles:
        filename = f"{article['id']}.json"
        filepath = os.path.join(articles_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(article, f, ensure_ascii=False, indent=2)
        saved_files.append(filename)

    # 更新索引文件
    index_path = os.path.join(articles_dir, "index.json")
    index: list[dict] = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

    for article in articles:
        # 避免重复
        existing_ids = {entry["id"] for entry in index}
        if article["id"] not in existing_ids:
            index.append({
                "id": article["id"],
                "title": article["title"],
                "category": article.get("category", "other"),
                "relevance_score": article.get("relevance_score", 0.5),
            })

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"[Saver] 保存 {len(saved_files)} 篇文章")
    print(f"[Saver] 本次运行总成本: ¥{tracker.get('total_cost_yuan', 0)}")
    return state
