"""主图组装（T047 v2：supervisor 动态路由 + create_agent 子图；D021/ADR-007）。

结构（architecture.md 5.2 v2）：

    __start__ → intent ─┬─ complex_consult → supervisor
                        │                      ├─ Command(goto=medical) → medical ┐（循环）
                        │                      ├─ Command(goto=claim)   → claim   ┘
                        │                      └─ Command(goto=FINISH)  → synthesize
                        ├─ simple_faq → rag → synthesize
                        └─ 其他（single_domain / chitchat / other）→ react → compliance
    synthesize → compliance
    compliance ─┬─ pass（PASS / MODIFY 达轮数上限）→ __end__
                ├─ modify（MODIFY 未达上限）→ revise_answer → compliance（复审闭环）
                └─ reject（REJECT）→ human_review（interrupt 挂起，坐席
                  Command(resume=结论) 恢复后经合规复审返回用户）→ __end__

- supervisor：结构化路由决策（RoutingDecision）+ Command 动态路由，支持执行中重规划
- claim / medical：create_agent Worker 子图包装节点（agents.runner.invoke_worker）
- react：create_agent 通用助手子图包装节点（工具循环内置）
- F10：所有输出路径必经 compliance 节点（条件边保证无旁路出口）
- Checkpoint：dev=InMemorySaver / prod=AsyncPostgresSaver；interrupt 挂起态同样持久化
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents import CLAIM_AGENT, MEDICAL_AGENT
from nodes.compliance import ComplianceNode, compliance_route, revise_answer_node
from nodes.generator import react_node, synthesize_answer_node
from nodes.human_review import HumanReviewNode
from nodes.intent import intent_node
from nodes.rag import rag_node
from nodes.supervisor import make_worker_node, supervisor_node
from state import AgentState
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def route_intent(state: AgentState) -> str:
    """意图分流条件边：complex_consult → supervisor；simple_faq → rag；其余 → react。"""
    intent = state.get("intent") or ""
    if intent == "complex_consult":  # v1 名 multi_step（D023 更名）
        return "supervisor"
    if intent == "simple_faq":
        return "rag"
    return "react"


def build_main_graph(
    executor: ToolExecutor,
    checkpointer: BaseCheckpointSaver,
) -> Any:
    """编译完整主图（intent 分流 + supervisor 调度 + 合规门禁）。

    Args:
        executor: 工具执行器（合规节点工具取证用；Worker/React 工具已自带守卫）
        checkpointer: Checkpoint saver（会话持久化键 = conversation_id 即 thread_id）
    """
    compliance = ComplianceNode(executor=executor)
    human_review = HumanReviewNode(executor=executor)

    builder = StateGraph(AgentState)
    builder.add_node("intent", intent_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("claim", make_worker_node(CLAIM_AGENT))
    builder.add_node("medical", make_worker_node(MEDICAL_AGENT))
    builder.add_node("rag", rag_node)
    builder.add_node("react", react_node)
    builder.add_node("synthesize", synthesize_answer_node)
    builder.add_node("compliance", compliance)
    builder.add_node("revise_answer", revise_answer_node)
    builder.add_node("human_review", human_review)

    builder.add_edge(START, "intent")
    # 意图分流（F03）
    builder.add_conditional_edges(
        "intent",
        route_intent,
        {"supervisor": "supervisor", "rag": "rag", "react": "react"},
    )
    # supervisor 动态路由（Command goto claim / medical / synthesize），Worker 完成回调度
    builder.add_edge("claim", "supervisor")
    builder.add_edge("medical", "supervisor")
    # RAG 路径：检索 → 整合（F02 完整）
    builder.add_edge("rag", "synthesize")
    # react 路径：子图内置工具循环 → 合规
    builder.add_edge("react", "compliance")
    # 整合后的回答必经合规（F10）
    builder.add_edge("synthesize", "compliance")
    # 合规三态流转
    builder.add_conditional_edges(
        "compliance",
        compliance_route,
        {"pass": END, "modify": "revise_answer", "reject": "human_review"},
    )
    builder.add_edge("revise_answer", "compliance")
    # T037：REJECT → human_review（interrupt 挂起，坐席 Command(resume) 恢复后出结论）
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=checkpointer)


def create_default_graph(
    registry: ToolRegistry | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """便捷工厂：默认注册中心 + 默认执行器 + 指定/内存 checkpointer。"""
    if registry is None:
        from tools.registry import get_default_registry

        registry = get_default_registry()
    executor = ToolExecutor(registry)
    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    return build_main_graph(executor, checkpointer)
