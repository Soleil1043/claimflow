"""Agent 定义装配：4 个 Agent 的统一出口与注册表。"""

from __future__ import annotations

from agents.base import AgentDefinition
from agents.claim import CLAIM_AGENT
from agents.compliance import COMPLIANCE_AGENT
from agents.medical import MEDICAL_AGENT
from agents.orchestrator import ORCHESTRATOR_AGENT

ALL_AGENTS: dict[str, AgentDefinition] = {
    a.name: a for a in (ORCHESTRATOR_AGENT, CLAIM_AGENT, MEDICAL_AGENT, COMPLIANCE_AGENT)
}


def get_agent(name: str) -> AgentDefinition:
    """按名获取 Agent 定义。"""
    return ALL_AGENTS[name]


__all__ = [
    "AgentDefinition",
    "ALL_AGENTS",
    "get_agent",
    "ORCHESTRATOR_AGENT",
    "CLAIM_AGENT",
    "MEDICAL_AGENT",
    "COMPLIANCE_AGENT",
]
