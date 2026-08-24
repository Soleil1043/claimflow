"""Agent 输出结构化 schema（T015：各 Agent 的 JSON 输出校验模型）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimAgentOutput(BaseModel):
    """Claim Agent 结构化输出。"""

    summary: str
    policy_info: dict = Field(default_factory=dict)
    calculation: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class MedicalAgentOutput(BaseModel):
    """Medical Agent 结构化输出。"""

    summary: str
    diagnosis: dict = Field(default_factory=dict)
    records: list[dict] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Violation(BaseModel):
    """单条违规项。"""

    type: str  # PROMISE / ABSOLUTE / MISLEAD / FRAUD_RISK / PRIVACY
    detail: str
    suggestion: str = ""


class ComplianceAgentOutput(BaseModel):
    """Compliance Agent 结构化输出（三态审查结论）。"""

    verdict: str  # PASS / MODIFY / REJECT
    violations: list[Violation] = Field(default_factory=list)
    risk_score: int = 0
    reason: str = ""


class PlanStep(BaseModel):
    """Orchestrator 计划单步。"""

    agent: str  # medical / claim
    description: str


class OrchestratorPlan(BaseModel):
    """Orchestrator 任务规划输出。"""

    intent: str
    steps: list[PlanStep] = Field(default_factory=list)
