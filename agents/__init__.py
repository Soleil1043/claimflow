"""Agent 定义与执行（Phase 8：仅保留核赔公共设施）。

AgentDefinition（agents/base.py）：静态定义（system_prompt / tools / output_schema）
invoke_worker / derive_tool_trace（agents/runner.py）：create_agent 官方子图执行
核赔阶段 Agent 定义在各阶段节点内联（nodes/liability_judge.py 等），不再集中管理。
"""

from agents.base import AgentDefinition
from agents.runner import derive_tool_trace, invoke_worker

__all__ = ["AgentDefinition", "derive_tool_trace", "invoke_worker"]
