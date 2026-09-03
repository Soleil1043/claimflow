"""schemas/case.py 案件图级输入/输出模型测试（T078）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas.case import CaseInput, CaseMaterialRef, CaseOutput


def test_case_input_parses_strong_types() -> None:
    """案件入参：金额 Decimal、日期 date、材料清单默认空。"""
    case = CaseInput(
        case_id="CASE-2026-0001",
        user_id="u-zhangwei",
        policy_id="POL-2025-0001",
        claimed_amount="15800.00",
        incident_date="2026-08-10",
        incident_description="急性阑尾炎住院手术",
        declared_case_type="medical",
        materials=[CaseMaterialRef(file_name="invoice.jpg", doc_type="invoice")],
    )
    assert case.claimed_amount == Decimal("15800.00")
    assert case.incident_date == date(2026, 8, 10)
    assert case.materials[0].doc_type == "invoice"
    assert case.materials[0].storage_path is None


def test_case_input_defaults_and_optional_declared_type() -> None:
    """declared_case_type 可空（客户未自报，intake 分类为准）。"""
    case = CaseInput(
        case_id="CASE-2026-0022",
        user_id="u-lina",
        policy_id="POL-2025-0002",
        claimed_amount="500000.00",
        incident_date="2026-08-28",
        incident_description="确诊重疾",
    )
    assert case.declared_case_type is None
    assert case.materials == []


def test_case_input_invalid_insurance_line_rejected() -> None:
    """险种枚举外的自报值拒错（ InsuranceLine 与 intake 分类目标同源）。"""
    with pytest.raises(ValidationError):
        CaseInput(
            case_id="X",
            user_id="u",
            policy_id="POL-2025-0001",
            claimed_amount="1.00",
            incident_date="2026-08-10",
            incident_description="x",
            declared_case_type="life",
        )


def test_case_output_minimal_terminal_state() -> None:
    """案件出参：终态字段可全空（转人工案件无决定书）。"""
    output = CaseOutput(case_id="CASE-2026-0001", case_type="medical")
    assert output.final_decision is None
    assert output.decision_document is None
    dumped = output.model_dump(mode="json")
    assert dumped["approved_amount"] is None
