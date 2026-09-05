"""核赔阶段产出模型（Phase 8，v2 架构文档第五节）。

每个阶段的唯一产出物（ClaimCaseState 以 model_dump 承载，字段所有权表见架构文档 5.3）：
- 金额一律 Decimal（序列化为 str 存储，严禁 float 参与计算）
- 日期一律 date
- 业务枚举用 Literal，结构化输出（with_structured_output）与子图 input/output schema 共用
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
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
    # 提取来源：vision（图片/扫描件识别）/ text_model（PDF/Word 文本提取）/
    # mock_fallback（失败降级）——与 services.materials 的 source 口径一致
    source: Literal["vision", "text_model", "mock_fallback"] = "vision"


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
    # partial 结论下的自费/乙类自付金额（理算扣减依据），covered/not_covered 为空
    self_pay_amount: Decimal | None = None
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


# ===== 阶段注册表（T094，D040）：阶段知识的唯一权威定义 =====
# 加阶段/改调度知识只动这里；orchestrator 守卫查表、图回边、路由快照、
# 路由 prompt 清单全部从 STAGE_SPECS 派生。compliance_gate 不进注册表——
# 它不是可调度阶段，是静态合规边（D039 铁律）。


class DispatchTarget(StrEnum):
    """orchestrator 可派发目标：六个 worker 阶段 + human（转人工）。"""

    MATERIAL_REVIEW = "material_review"
    POLICY_VERIFY = "policy_verify"
    FRAUD_CHECK = "fraud_check"
    LIABILITY_JUDGE = "liability_judge"
    AMOUNT_CALC = "amount_calc"
    DECISION_GENERATE = "decision_generate"
    HUMAN = "human"


WORKER_TARGETS: tuple[DispatchTarget, ...] = tuple(
    t for t in DispatchTarget if t is not DispatchTarget.HUMAN
)


@dataclass(frozen=True)
class StageSpec:
    """单个阶段的权威定义。

    - requires：前置 worker，缺失时守卫改投第一个未完成项（按声明顺序）
    - snapshot_keys：路由快照摘要字段；空 = 快照仅显示 done/None
    - back_to_orchestrator：worker 完成后是否静态回边 orchestrator
      （decision_generate 例外——静态进合规链，D039）
    - in_must_complete：是否属于 decision_generate 的必做集
    """

    name: DispatchTarget
    channel: str
    output_model: type[BaseModel]
    requires: tuple[DispatchTarget, ...] = ()
    snapshot_keys: tuple[str, ...] = ()
    in_must_complete: bool = True
    back_to_orchestrator: bool = True
    description: str = ""


STAGE_SPECS: tuple[StageSpec, ...] = (
    StageSpec(
        name=DispatchTarget.MATERIAL_REVIEW,
        channel="material",
        output_model=MaterialReviewOutput,
        snapshot_keys=("completeness", "missing", "confidence"),
        description="材料审核（OCR/解析、完整性校验）——案件起点",
    ),
    StageSpec(
        name=DispatchTarget.POLICY_VERIFY,
        channel="policy",
        output_model=PolicyVerifyOutput,
        requires=(DispatchTarget.MATERIAL_REVIEW,),
        snapshot_keys=("coverage_valid", "waiting_period_passed", "invalid_reason"),
        description="保单核验（有效性/等待期/除外/限额）——需材料完整",
    ),
    StageSpec(
        name=DispatchTarget.FRAUD_CHECK,
        channel="risk",
        output_model=FraudCheckOutput,
        requires=(DispatchTarget.MATERIAL_REVIEW,),
        snapshot_keys=("risk_level", "risk_score"),
        description="风控筛查（黑名单/理赔频率/可疑模式）——需材料完整，可与 policy_verify 同轮并行",
    ),
    StageSpec(
        name=DispatchTarget.LIABILITY_JUDGE,
        channel="liability",
        output_model=LiabilityOutput,
        requires=(DispatchTarget.POLICY_VERIFY, DispatchTarget.FRAUD_CHECK),
        snapshot_keys=("verdict", "confidence"),
        description="责任认定（条款匹配/除外排查/置信度）——需保单与风控结论",
    ),
    StageSpec(
        name=DispatchTarget.AMOUNT_CALC,
        channel="calc",
        output_model=AmountCalcOutput,
        requires=(DispatchTarget.LIABILITY_JUDGE,),
        snapshot_keys=("approved_amount",),
        description="金额理算（确定性计算）——需责任认定结论",
    ),
    StageSpec(
        name=DispatchTarget.DECISION_GENERATE,
        channel="decision",
        output_model=DecisionDocOutput,
        in_must_complete=False,
        back_to_orchestrator=False,
        description="决定书生成——需理算结论，且必须单独派发",
    ),
)

STAGE_CHANNELS: dict[DispatchTarget, str] = {s.name: s.channel for s in STAGE_SPECS}
STAGE_SPECS_BY_NAME: dict[DispatchTarget, StageSpec] = {s.name: s for s in STAGE_SPECS}
# decision_generate（终局派发）的必做集：所有 in_must_complete 阶段
MUST_COMPLETE: tuple[DispatchTarget, ...] = tuple(
    s.name for s in STAGE_SPECS if s.in_must_complete
)

HUMAN_TARGET_DESCRIPTION = "转人工（缺件补件 / 高风险 / 置信度低 / 材料矛盾 / 规则未覆盖）"


def render_dispatch_catalog() -> str:
    """路由 prompt 的可派发目标清单（注册表派生，prompt 与运行时不漂移）。"""
    lines = [f"- {s.name}：{s.description}" for s in STAGE_SPECS]
    lines.append(f"- {DispatchTarget.HUMAN}：{HUMAN_TARGET_DESCRIPTION}")
    return "\n".join(lines)
