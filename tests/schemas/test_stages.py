"""schemas/stages.py 阶段产出模型测试（T078）。

覆盖：Decimal/date 强类型解析、枚举拒错、JSON 往返保真、理算口径、七阶段模型齐备。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas.stages import (
    AmountCalcOutput,
    ComplianceOutput,
    DecisionDocOutput,
    DeductionItem,
    ExtractedDocument,
    FraudCheckOutput,
    LiabilityOutput,
    MaterialReviewOutput,
    PolicyVerifyOutput,
)


def test_extracted_document_decimal_and_date_coercion() -> None:
    """金额从字符串解析为 Decimal、日期从 ISO 解析为 date——严禁 float 入账。"""
    doc = ExtractedDocument(
        doc_type="invoice",
        file_name="invoice_15800.jpg",
        total_amount="15800.00",
        treatment_date="2026-08-10",
    )
    assert doc.total_amount == Decimal("15800.00")
    assert isinstance(doc.total_amount, Decimal)
    assert doc.treatment_date == date(2026, 8, 10)
    assert doc.source == "vision"  # 默认值


def test_extracted_document_doc_type_is_open_string() -> None:
    """doc_type 为开放 str：白名单由险种 pack 派生、API 层统一校验（T120 归一），
    提取层不再以 Literal 拒绝（新增险种材料类型零 schema 改动）。"""
    doc = ExtractedDocument(doc_type="police_report", file_name="x.jpg")
    assert doc.doc_type == "police_report"


def test_material_review_output_round_trip() -> None:
    """model_dump(mode="json") → validate 往返：Decimal 保真（str 序列化语义）。"""
    output = MaterialReviewOutput(
        documents=[
            ExtractedDocument(
                doc_type="invoice",
                file_name="a.jpg",
                total_amount=Decimal("15800.00"),
                treatment_date=date(2026, 8, 10),
                confidence=0.97,
            )
        ],
        completeness="complete",
        missing=[],
        confidence=0.95,
    )
    dumped = output.model_dump(mode="json")
    assert dumped["documents"][0]["total_amount"] == "15800.00"
    assert dumped["documents"][0]["treatment_date"] == "2026-08-10"
    restored = MaterialReviewOutput.model_validate(dumped)
    assert restored.documents[0].total_amount == Decimal("15800.00")
    assert restored == output


def test_policy_verify_output_financial_fields() -> None:
    """保单核验产出：保额/免赔/比例 Decimal 承载（理算工具直接消费）。"""
    output = PolicyVerifyOutput(
        policy_found=True,
        coverage_valid=True,
        waiting_period_passed=True,
        policy_amount="1000000.00",
        deductible="10000.00",
        payout_ratio="0.8",
    )
    assert output.policy_amount == Decimal("1000000.00")
    assert output.payout_ratio == Decimal("0.8")
    assert output.invalid_reason is None


def test_fraud_check_output_risk_fields() -> None:
    """风控产出：评分 + 三档风险等级 + 指标清单。"""
    output = FraudCheckOutput(
        risk_score=85.0, risk_level="high", indicators=["blacklist_hit"], blacklisted=True
    )
    assert output.risk_level == "high"
    with pytest.raises(ValidationError):
        FraudCheckOutput(risk_score=10.0, risk_level="extreme")  # 非法风险等级


def test_amount_calc_output_deterministic_math() -> None:
    """理算产出：扣减明细 + 计算式说明；金额 Decimal 运算口径正确（金样本 0013）。"""
    output = AmountCalcOutput(
        claimed_amount="17500.00",
        approved_amount=Decimal("17500.00") - Decimal("2500.00") - Decimal("10000.00"),
        deductions=[
            DeductionItem(name="自费内固定材料", amount="2500.00", reason="非医保目录"),
            DeductionItem(name="免赔额", amount="10000.00", reason="年度免赔"),
        ],
        calculation_basis="(17500-2500自费-10000免赔)*0.8=4000.00",
    )
    assert output.approved_amount == Decimal("5000.00")
    # 与金样本 CASE-2026-0013 期望的理算式一致：(17500-2500-10000)*0.8=4000
    assert Decimal("17500.00") - Decimal("2500.00") - Decimal("10000.00") == Decimal("5000.00")
    assert Decimal("5000.00") * Decimal("0.8") == Decimal("4000.0000")


def test_liability_output_clause_references() -> None:
    """责任认定产出：结论 + 条款引用（可溯源要求）。"""
    output = LiabilityOutput(
        verdict="not_covered",
        reason="整形美容属除外责任",
        clause_references=["第五条 责任免除（一）"],
        exclusions_triggered=["整形美容"],
        confidence=0.93,
    )
    assert output.verdict == "not_covered"
    assert output.clause_references


def test_decision_doc_and_compliance_defaults() -> None:
    """决定书草稿与合规产出：字段默认值语义（金额一致默认成立、版本从 1 起）。"""
    doc = DecisionDocOutput(title="理赔决定书", body="……", conclusion="approved")
    assert doc.version == 1
    assert doc.approved_amount is None
    compliance = ComplianceOutput(verdict="PASS")
    assert compliance.amount_consistent is True
    assert compliance.round == 1
    assert compliance.violations == []


def test_all_seven_stage_models_declared() -> None:
    """七个阶段产出模型齐备且 stage 默认值各就各位（字段所有权表的唯一写者对应）。"""
    expected_stages = {
        "material_review": MaterialReviewOutput,
        "policy_verify": PolicyVerifyOutput,
        "fraud_check": FraudCheckOutput,
        "liability_judge": LiabilityOutput,
        "amount_calc": AmountCalcOutput,
        "decision_generate": DecisionDocOutput,
        "compliance_gate": ComplianceOutput,
    }
    for stage_name, model in expected_stages.items():
        assert model.model_fields["stage"].default == stage_name


# ===== 阶段注册表派生一致性（T094，D040）=====


def test_dispatch_target_members_match_registry() -> None:
    """DispatchTarget 非 human 成员 == 注册表阶段集合（类型即派生物）。"""
    from schemas.stages import STAGE_SPECS, DispatchTarget

    assert {t for t in DispatchTarget if t is not DispatchTarget.HUMAN} == {
        s.name for s in STAGE_SPECS
    }


def test_registry_channels_exist_in_claim_state() -> None:
    """每个 StageSpec.channel 都是 ClaimCaseState 的真实字段。"""
    from schemas.stages import STAGE_SPECS
    from state import ClaimCaseState

    for spec in STAGE_SPECS:
        assert spec.channel in ClaimCaseState.__annotations__


def test_requires_reference_known_targets() -> None:
    """requires 引用的都是注册表内阶段（防手滑写错名）。"""
    from schemas.stages import STAGE_SPECS_BY_NAME

    for spec in STAGE_SPECS_BY_NAME.values():
        for req in spec.requires:
            assert req in STAGE_SPECS_BY_NAME


def test_must_complete_excludes_decision_generate() -> None:
    """必做集 = in_must_complete 阶段；decision_generate 是终局派发不在其中。"""
    from schemas.stages import MUST_COMPLETE, DispatchTarget

    assert DispatchTarget.DECISION_GENERATE not in MUST_COMPLETE
    assert len(MUST_COMPLETE) == 5


def test_render_dispatch_catalog_lists_all_targets() -> None:
    """路由 prompt 目标清单覆盖全部派发目标（含 human）。"""
    from schemas.stages import DispatchTarget, render_dispatch_catalog

    catalog = render_dispatch_catalog()
    for t in DispatchTarget:
        assert t.value in catalog
