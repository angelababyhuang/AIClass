"""
LangGraph 工作流图定义 — 知识库采集-分析-审核流水线

【核心教学点: Planner + Review Loop】

工作流拓扑:

    plan → collect → analyze → organize → review ─→ save (通过)
                                   ↑                   │
                                   └───────────────────┘ (未通过，按 plan.max_iterations 重试)

- plan 节点：Planner 模式，只规划不执行。输出 state["plan"]，下游节点据此行事
- 审核循环通过 add_conditional_edges 实现：
  - review_passed == True  → 进入 save 节点
  - review_passed == False → 回到 organize 节点（带反馈修正，由 _organize_with_feedback 承担 Reviser 职责）
  - iteration >= plan.max_iterations → 强制进入 save（在 review_node 内处理）
"""

from langgraph.graph import END, StateGraph

from patterns.planner import planner_node
from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    review_node,
    save_node,
)
from workflows.state import KBState


def should_continue(state: KBState) -> str:
    """条件路由函数：决定审核后的下一步

    这是 Review Loop 的关键决策点。
    LangGraph 在 review 节点之后调用此函数，根据返回值选择分支:
    - "save"     → 审核通过，保存结果
    - "organize" → 审核未通过，回到整理节点修正

    注意: 最大迭代次数（3次）的强制通过逻辑在 review_node 内处理，
    这里只需要读取 review_passed 的值。
    """
    if state.get("review_passed", False):
        return "save"
    else:
        return "organize"


def build_graph() -> StateGraph:
    """构建知识库工作流图

    Returns:
        编译后的 LangGraph 应用，可通过 app.invoke() 或 app.stream() 执行
    """
    # --- 1. 创建状态图 ---
    graph = StateGraph(KBState)

    # --- 2. 添加节点 ---
    graph.add_node("plan", planner_node)
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("save", save_node)

    # --- 3. 添加边 ---
    # 线性流: plan → collect → analyze → organize → review
    graph.add_edge("plan", "collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")

    # 【重点】条件边: review 之后根据审核结果分支
    # - "save"     → 保存（审核通过）
    # - "organize" → 重新整理（审核未通过，带反馈修正）
    graph.add_conditional_edges(
        "review",           # 源节点
        should_continue,    # 路由函数
        {
            "save": "save",         # 审核通过 → 保存
            "organize": "organize",  # 审核未通过 → 回到整理
        },
    )

    # save 之后结束
    graph.add_edge("save", END)

    # --- 4. 设置入口 ---
    graph.set_entry_point("plan")

    return graph


# --- 编译图，暴露 app 供外部调用 ---
app = build_graph().compile()


# --- 便捷运行入口 ---
if __name__ == "__main__":
    print("=" * 60)
    print("AI 知识库 — LangGraph 工作流启动")
    print("=" * 60)

    # 初始状态
    initial_state: KBState = {
        "plan": {},
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
        "cost_tracker": {},
    }

    # 跟踪 plan 用于显示正确的 max_iter
    current_plan: dict = {}

    # 流式执行，观察每个节点的输出
    for event in app.stream(initial_state):
        node_name = list(event.keys())[0]
        print(f"\n--- [{node_name}] 完成 ---")

        # 打印关键信息
        node_output = event[node_name]
        if "plan" in node_output:
            current_plan = node_output["plan"] or {}
            print(f"  策略: {current_plan.get('strategy', '?')}")
        if "sources" in node_output:
            print(f"  采集数量: {len(node_output['sources'])}")
        if "analyses" in node_output:
            print(f"  分析数量: {len(node_output['analyses'])}")
        if "articles" in node_output:
            print(f"  文章数量: {len(node_output['articles'])}")
        if "review_passed" in node_output:
            max_iter = current_plan.get("max_iterations", 3)
            print(f"  审核结果: {'通过' if node_output['review_passed'] else '未通过'}")
            print(f"  迭代次数: {node_output.get('iteration', '?')}/{max_iter}")
        if "cost_tracker" in node_output:
            cost = node_output["cost_tracker"].get("total_cost_yuan", 0)
            print(f"  累计成本: ¥{cost}")

    print("\n" + "=" * 60)
    print("工作流执行完毕")
    print("=" * 60)
