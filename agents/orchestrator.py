"""Orchestrator Agent（调度代理）：意图识别 + 任务规划 + 结果整合（T017 实现执行逻辑）。"""

from __future__ import annotations

from agents.base import AgentDefinition
from schemas.agent_outputs import OrchestratorPlan
from services.llm.prompts import ORCHESTRATOR_AGENT_PROMPT

ORCHESTRATOR_AGENT = AgentDefinition(
    name="orchestrator",
    display_name="调度 Agent",
    system_prompt=ORCHESTRATOR_AGENT_PROMPT,
    # Orchestrator 不直接调用业务工具（意图/规划/整合均走 LLM 结构化输出）；
    # simple_faq 分流后的 RAG 检索由 rag 节点承担（T021 组图）
    tool_names=[],
    output_schema=OrchestratorPlan,
    description="理解用户意图、制定多步执行计划、整合各 Worker 结果生成最终回答",
)
