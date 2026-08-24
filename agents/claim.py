"""Claim Agent（理赔核算代理）：保单查询 / 赔付计算 / 理赔规则检索。"""

from __future__ import annotations

from agents.base import AgentDefinition
from schemas.agent_outputs import ClaimAgentOutput
from services.llm.prompts import CLAIM_AGENT_PROMPT

CLAIM_AGENT = AgentDefinition(
    name="claim",
    display_name="理赔核算 Agent",
    system_prompt=CLAIM_AGENT_PROMPT,
    tool_names=[
        "policy_query",
        "claim_calculator",
        "claim_rule_rag",
        # claim_status_query 随 T016 任务一并实现（理赔进度查询）
        "claim_status_query",
    ],
    output_schema=ClaimAgentOutput,
    description="查询保单详情、核算预估赔付金额、检索理赔规则",
)
