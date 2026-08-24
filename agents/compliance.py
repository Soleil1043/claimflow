"""Compliance Agent（合规风控代理）：一票否决权（ADR-002：独立 Agent 而非工具）。

审查工具 rule_check / risk_scoring / sensitive_filter 随 T018/T019 实现。
"""

from __future__ import annotations

from agents.base import AgentDefinition
from schemas.agent_outputs import ComplianceAgentOutput
from services.llm.prompts import COMPLIANCE_AGENT_PROMPT

COMPLIANCE_AGENT = AgentDefinition(
    name="compliance",
    display_name="合规风控 Agent",
    system_prompt=COMPLIANCE_AGENT_PROMPT,
    tool_names=[
        # 以下工具随 T018/T019 实现
        "compliance_rule_check",
        "risk_scoring",
        "sensitive_filter",
    ],
    output_schema=ComplianceAgentOutput,
    description="审查输出内容合规性（违规话术/欺诈风险/隐私泄露），拥有 PASS/MODIFY/REJECT 三态一票否决权",
)
