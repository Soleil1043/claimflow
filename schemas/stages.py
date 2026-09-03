"""核赔阶段产出模型（Phase 8，v2 架构文档第五节）。

每个阶段的唯一产出物（ClaimCaseState 以 model_dump 承载，字段所有权表见架构文档 5.3）：
- 金额一律 Decimal（序列化为 str 存储，严禁 float 参与计算）
- 日期一律 date
- 业务枚举用 Literal，结构化输出（with_structured_output）与子图 input/output schema 共用
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

# ===== 共享枚举 =====
InsuranceLine = Literal["medical", "auto", "property", "accident"]
Completeness = Literal["complete", "partial"]
RiskLevel = Literal["low", "medium", "high"]
LiabilityVerdict = Literal["covered", "not_covered", "partial"]
FinalDecision = Literal["approved", "rejected", "partial", "referred"]
ComplianceVerdict = Literal["PASS", "MODIFY", "REJECT"]


class ExtractedDocument(BaseModel):
    """单份材料的结构化提取结果（OCR/文本解析产出）。"""

    doc_type: Literal["invoice", "diagnosis", "cost_list", "medical_record"]
    file_name: str
    patient_name: str | None = None
    hospital: str | None = None
    diagnosis: str | None = None
    total_amount: Decimal | None = None
    treatment_date: date | None = None
    confidence: float = 0.0
    # 提取来源：vision（图片 OCR）/ text（文本解析）/ mock_fallback（兜底，D008 语义延续）
    source: Literal["vision", "text", "mock_fallback"] = "vision"


class MaterialReviewOutput(BaseModel):
    """材料审核阶段产出：提取结果 + 完整性结论。"""

    stage: Literal["material_review"] = "material_review"
    documents: list[ExtractedDocument] = Field(default_factory=list)
    completeness: Completeness
    missing: list[str] = Field(default_factory=list)
    confidence: float


class PolicyVerifyOutput(BaseModel):
    """保单核验阶段产出：有效性 + 关键条款要素（理算依据）。"""

    stage: Literal["policy_verify"] = "policy_verify"
    policy_found: bool
    coverage_valid: bool
    waiting_period_passed: bool | None = None
    coverage_scope: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    # 保额 / 免赔额 / 赔付比例（0-1）——理算工具直接消费
    policy_amount: Decimal | None = None
    deductible: Decimal | None = None
    payout_ratio: Decimal | None = None
    invalid_reason: str | None = None
    confidence: float = 1.0


class FraudCheckOutput(BaseModel):
    """风控筛查阶段产出：规则评分 + 触发指标。"""

    stage: Literal["fraud_check"] = "fraud_check"
    risk_score: float  # 0-100
    risk_level: RiskLevel
    indicators: list[str] = Field(default_factory=list)
    blacklisted: bool = False
    recent_claims_count: int = 0


class LiabilityOutput(BaseModel):
    """责任认定阶段产出：结论 + 条款依据（可溯源）。"""

    stage: Literal["liability_judge"] = "liability_judge"
    verdict: LiabilityVerdict
    reason: str
    clause_references: list[str] = Field(default_factory=list)
    exclusions_triggered: list[str] = Field(default_factory=list)
    confidence: float


class DeductionItem(BaseModel):
    """理算扣减明细项。"""

    name: str
    amount: Decimal
    reason: str


class AmountCalcOutput(BaseModel):
    """金额理算阶段产出（确定性工具，无 LLM）。"""

    stage: Literal["amount_calc"] = "amount_calc"
    claimed_amount: Decimal
    approved_amount: Decimal
    deductions: list[DeductionItem] = Field(default_factory=list)
    # 计算式说明，如 "(17500-2500自费-10000免赔)*0.8=4000.00"
    calculation_basis: str
    confidence: float = 1.0


class DecisionDocOutput(BaseModel):
    """决定书草稿产出（模板 + LLM 撰写）。正文金额必须等于理算结果（F10 断言）。"""

    stage: Literal["decision_generate"] = "decision_generate"
    title: str
    body: str
    # 决定书结论（referred 不是决定书结论——转人工无文书）
    conclusion: Literal["approved", "rejected", "partial"]
    approved_amount: Decimal | None = None
    version: int = 1


class ComplianceOutput(BaseModel):
    """合规门产出：三态裁决 + 金额一致性断言结果。"""

    stage: Literal["compliance_gate"] = "compliance_gate"
    verdict: ComplianceVerdict
    violations: list[str] = Field(default_factory=list)
    suggestion: str | None = None
    risk_score: float = 0.0
    # 决定书正文金额 == calc.approved_amount 的机器断言结果（不依赖 LLM）
    amount_consistent: bool = True
    round: int = 1
