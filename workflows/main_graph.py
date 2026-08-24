"""主图组装（T021 完整版：intent 分流 + 多 Agent 协作 + 合规门禁）。

结构（architecture.md 5.2）：

    __start__ → intent ─┬─ multi_step → planner → step_executor ─┬─ next → step_executor（循环）
                        │                                        └─ done → synthesize
                        ├─ simple_faq → rag_node → synthesize
                        └─ 其他（single_domain / chitchat / other）→ react_agent
                                                                     ┌─ tools 轮（条件边）→ react_agent（循环）
                                                                     └─ end → compliance
    synthesize → compliance
    compliance ─┬─ pass（PASS / MODIFY 达轮数上限）→ __end__
                ├─ modify（MODIFY 未达上限）→ revise_answer → compliance（复审闭环）
                └─ reject（REJECT：内容不返回用户，标记转人工）→ __end__

F10：所有输出路径必经 compliance 节点（条件边保证无旁路出口）。
Checkpoint：dev=InMemorySaver / prod=AsyncPostgresSaver（CheckpointManager 管理，
F14 同一会话多轮上下文连贯，服务重启后可恢复）。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from nodes.compliance import ComplianceNode, compliance_route, revise_answer_node
from nodes.generator import ReactAgentNode, should_continue, synthesize_answer_node
from nodes.intent import intent_node
from nodes.planner import planner_node
from nodes.rag import rag_node
from nodes.step_executor import StepExecutorNode, has_next_step
from state import AgentState
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def route_intent(state: AgentState) -> str:
    """意图分流条件边：multi_step → planner；simple_faq → rag；其余 → react。"""
    intent = state.get("intent") or ""
    if intent == "multi_step":
        return "planner"
    if intent == "simple_faq":
        return "rag"
    return "react"


def build_main_graph(
    executor: ToolExecutor,
    checkpointer: BaseCheckpointSaver,
) -> Any:
    """编译完整主图（intent 分流 + 多 Agent + 合规门禁）。

    Args:
        executor: 工具执行器（注册中心从中取）
        checkpointer: Checkpoint saver（会话持久化键 = conversation_id 即 thread_id）
    """
    react_agent = ReactAgentNode(executor=executor)
    step_executor = StepExecutorNode(executor=executor)
    compliance = ComplianceNode(executor=executor)

    builder = StateGraph(AgentState)
    builder.add_node("intent", intent_node)
    builder.add_node("planner", planner_node)
    builder.add_node("step_executor", step_executor)
    builder.add_node("rag", rag_node)
    builder.add_node("react_agent", react_agent)
    builder.add_node("synthesize", synthesize_answer_node)
    builder.add_node("compliance", compliance)
    builder.add_node("revise_answer", revise_answer_node)

    builder.add_edge(START, "intent")
    # 意图分流（F03）
    builder.add_conditional_edges(
        "intent",
        route_intent,
        {"planner": "planner", "rag": "rag", "react": "react_agent"},
    )
    # 多步路径：规划 → 逐步执行循环 → 整合（F08）
    builder.add_edge("planner", "step_executor")
    builder.add_conditional_edges(
        "step_executor",
        has_next_step,
        {"next": "step_executor", "done": "synthesize"},
    )
    # RAG 路径：检索 → 整合（F02 完整）
    builder.add_edge("rag", "synthesize")
    # ReAct 路径：工具循环（F07）
    builder.add_conditional_edges(
        "react_agent",
        should_continue,
        {"tools": "react_agent", "end": "compliance"},
    )
    # 整合后的回答必经合规（F10）
    builder.add_edge("synthesize", "compliance")
    # 合规三态流转
    builder.add_conditional_edges(
        "compliance",
        compliance_route,
        {"pass": END, "modify": "revise_answer", "reject": END},
    )
    builder.add_edge("revise_answer", "compliance")
    return builder.compile(checkpointer=checkpointer)


def create_default_graph(
    registry: ToolRegistry | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """便捷工厂：默认注册中心 + 默认执行器 + 指定/内存 checkpointer。"""
    if registry is None:
        import tools.claim  # noqa: F401 注册理赔工具
        import tools.compliance  # noqa: F401 注册合规工具
        import tools.medical  # noqa: F401 注册医疗工具
        from tools.registry import get_default_registry

        registry = get_default_registry()
    executor = ToolExecutor(registry)
    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    return build_main_graph(executor, checkpointer)
