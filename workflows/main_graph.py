"""主图组装（Phase 1 简版：单 Agent ReAct，F07）。

结构：
    __start__ → react_agent ─┬─ tools 轮（条件边：末尾是 ToolMessage）→ react_agent（循环）
                             └─ end（产出最终回答）→ __end__

T013（意图识别）/ T015+（多 Agent）在同一图上扩展节点与条件边。
Checkpoint：dev=InMemorySaver / prod=AsyncPostgresSaver（CheckpointManager 管理，
F14 同一会话多轮上下文连贯）。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from nodes.generator import ReactAgentNode, should_continue
from state import AgentState
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def build_phase1_graph(
    executor: ToolExecutor,
    checkpointer: BaseCheckpointSaver,
) -> Any:
    """编译 Phase 1 主图。

    Args:
        executor: 工具执行器（注册中心从中取）
        checkpointer: Checkpoint saver（会话持久化键 = conversation_id 即 thread_id）
    """
    agent = ReactAgentNode(executor=executor)

    builder = StateGraph(AgentState)
    builder.add_node("react_agent", agent)
    builder.add_edge(START, "react_agent")
    builder.add_conditional_edges(
        "react_agent",
        should_continue,
        {"tools": "react_agent", "end": END},
    )
    return builder.compile(checkpointer=checkpointer)


def create_default_graph(
    registry: ToolRegistry | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """便捷工厂：默认注册中心 + 默认执行器 + 指定/内存 checkpointer。"""
    if registry is None:
        import tools.claim  # noqa: F401 注册理赔工具
        from tools.registry import get_default_registry

        registry = get_default_registry()
    executor = ToolExecutor(registry)
    if checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    return build_phase1_graph(executor, checkpointer)
