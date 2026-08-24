"""主图组装（Phase 1 简版 + 合规门禁，F07/F10）。

结构（T018 起所有输出路径必经合规节点）：

    __start__ → react_agent ─┬─ tools 轮（条件边：末尾是 ToolMessage）→ react_agent（循环）
                             └─ end（产出最终回答）→ compliance
    compliance ─┬─ pass（PASS / MODIFY 达轮数上限）→ __end__
                ├─ modify（MODIFY 未达上限）→ revise_answer → compliance（复审闭环）
                └─ reject（REJECT：内容不返回用户，标记转人工）→ __end__

T013（意图识别）/ T015+（多 Agent）在同一图上扩展节点与条件边（T021 完整组装）。
Checkpoint：dev=InMemorySaver / prod=AsyncPostgresSaver（CheckpointManager 管理，
F14 同一会话多轮上下文连贯）。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from nodes.compliance import ComplianceNode, compliance_route, revise_answer_node
from nodes.generator import ReactAgentNode, should_continue
from state import AgentState
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def build_phase1_graph(
    executor: ToolExecutor,
    checkpointer: BaseCheckpointSaver,
) -> Any:
    """编译 Phase 1 主图（ReAct 循环 + 合规三态门禁）。

    Args:
        executor: 工具执行器（注册中心从中取）
        checkpointer: Checkpoint saver（会话持久化键 = conversation_id 即 thread_id）
    """
    agent = ReactAgentNode(executor=executor)
    compliance = ComplianceNode(executor=executor)

    builder = StateGraph(AgentState)
    builder.add_node("react_agent", agent)
    builder.add_node("compliance", compliance)
    builder.add_node("revise_answer", revise_answer_node)
    builder.add_edge(START, "react_agent")
    # react_agent 的最终回答必经 compliance（F10：条件边保证无旁路出口）
    builder.add_conditional_edges(
        "react_agent",
        should_continue,
        {"tools": "react_agent", "end": "compliance"},
    )
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
        from tools.registry import get_default_registry

        registry = get_default_registry()
    executor = ToolExecutor(registry)
    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    return build_phase1_graph(executor, checkpointer)
