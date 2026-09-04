"""services/decision_doc 决定书构建服务测试（T085）：金额安全设计 / 三层审查。"""

from __future__ import annotations

import json
from decimal import Decimal

from services.decision_doc import (
    conclusion_for,
    fallback_narrative,
    render_decision_document,
    review_decision_document,
)

_LIABILITY = {
    "verdict": "covered",
    "reason": "急性阑尾炎手术属住院医疗责任",
    "clause_references": ["第三条 保险责任"],
}
_CALC = {
    "approved_amount": "4640.00",
    "calculation_basis": "(15800-10000免赔)*0.8=4640.00",
    "deductions": [{"name": "免赔额", "amount": "10000.00"}],
}


def _render(**kwargs) -> dict:
    return render_decision_document(
        case_id="CASE-2026-0001",
        case_type="medical",
        liability=_LIABILITY,
        calc=_CALC,
        **kwargs,
    )


def test_conclusion_for_mapping() -> None:
    """责任结论 → 决定书结论映射；referred 不是决定书结论。"""
    assert conclusion_for("covered") == "approved"
    assert conclusion_for("partial") == "partial"
    assert conclusion_for("not_covered") == "rejected"


def test_render_injects_amount_from_calc() -> None:
    """金额安全设计：正文金额来自理算结构化数据（代码注入，非 LLM）。"""
    doc = _render(narrative="经审核，属于保障范围。")
    assert doc["approved_amount"] == "4640.00"
    assert "核定金额：4640.00 元" in doc["body"]
    assert "4640.00" in doc["body"]
    assert doc["conclusion"] == "approved"


def test_render_masks_pii() -> None:
    """正文脱敏：叙述中混入身份证号 → 出站前打码（F10 第四层）。"""
    doc = _render(narrative="被保险人身份证号 330106199203154817 已核验。")
    assert "330106199203154817" not in doc["body"]
    assert "********" in doc["body"]


def test_render_fallback_narrative_when_none() -> None:
    """叙述缺失 → 规则版兜底叙述（含责任原因）。"""
    doc = _render(narrative=None)
    assert "经审核，" in doc["body"]


def test_render_rejected_zero_amount() -> None:
    """拒赔决定书：核定金额固定 0.00（无论理算输入）。"""
    liability = {**_LIABILITY, "verdict": "not_covered"}
    doc = render_decision_document(
        case_id="C", case_type="medical", liability=liability,
        calc={**_CALC, "approved_amount": "999.00"}, narrative=None,
    )
    assert doc["conclusion"] == "rejected"
    assert doc["approved_amount"] == "0.00"


# ---------- 三层审查 ----------


def test_review_pass_when_consistent() -> None:
    doc = _render(narrative="经审核，属于保障范围。")
    review = review_decision_document(doc, _CALC)
    assert review["verdict"] == "PASS"
    assert review["amount_consistent"] is True
    assert review["violations"] == []


def test_review_amount_mismatch_modify() -> None:
    """金额注入攻击：正文/结构金额被篡改 → MODIFY（代码重渲染修复）。"""
    doc = _render(narrative="经审核，属于保障范围。")
    tampered = {**doc, "body": doc["body"].replace("4640.00", "9999.00"),
                "approved_amount": "9999.00"}
    review = review_decision_document(tampered, _CALC)
    assert review["verdict"] == "MODIFY"
    assert review["amount_consistent"] is False
    assert any(v["type"] == "amount_mismatch" for v in review["violations"])


def test_review_body_only_tamper_detected() -> None:
    """仅正文数字被改（结构字段未动）→ 正文提取断言也能抓到。"""
    doc = _render(narrative="经审核，属于保障范围。")
    tampered = {**doc, "body": doc["body"].replace("核定金额：4640.00 元",
                                                   "核定金额：10000.00 元")}
    review = review_decision_document(tampered, _CALC)
    assert review["verdict"] == "MODIFY"


def test_review_red_line_reject() -> None:
    """红线话术（违规承诺）→ REJECT 转人工（对外文书零容忍）。"""
    doc = _render(narrative="本公司保证赔付，百分百全额理赔。")
    review = review_decision_document(doc, _CALC)
    assert review["verdict"] == "REJECT"
    assert any(v["type"] != "amount_mismatch" for v in review["violations"])


def test_fallback_narrative_mentions_reason_and_clauses() -> None:
    narrative = fallback_narrative(_LIABILITY, _CALC)
    assert "急性阑尾炎手术属住院医疗责任" in narrative
    assert "第三条 保险责任" in narrative


def test_review_doc_json_serializable() -> None:
    doc = _render(narrative="经审核，属于保障范围。")
    review = review_decision_document(doc, _CALC)
    json.dumps(doc, ensure_ascii=False), json.dumps(review, ensure_ascii=False)
    assert Decimal(doc["approved_amount"]) == Decimal("4640.00")
