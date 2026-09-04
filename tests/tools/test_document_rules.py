"""tools/document 规则层纯函数测试（T082）：完整性/分类/金额交叉核验。"""

from __future__ import annotations

from tools.document.classify import find_amount_contradictions, infer_doc_type
from tools.document.completeness import validate_completeness


def _doc(doc_type: str, amount: str | None = None) -> dict:
    return {"doc_type": doc_type, "total_amount": amount}


# ---------- 完整性 ----------


def test_completeness_medical_complete() -> None:
    docs = [_doc("invoice"), _doc("diagnosis"), _doc("cost_list"), _doc("medical_record")]
    result = validate_completeness(docs, "medical")
    assert result == {"completeness": "complete", "missing": []}


def test_completeness_medical_partial() -> None:
    docs = [_doc("invoice"), _doc("diagnosis")]
    result = validate_completeness(docs, "medical")
    assert result["completeness"] == "partial"
    assert result["missing"] == ["费用清单"]


def test_completeness_unknown_line_treated_complete() -> None:
    """未上线险种无清单 → complete（该类案件受理期即转人工，不消费此规则）。"""
    assert validate_completeness([], "accident")["completeness"] == "complete"


def test_completeness_empty_documents_partial() -> None:
    result = validate_completeness([], "medical")
    assert result["completeness"] == "partial"
    assert len(result["missing"]) == 3


# ---------- 分类 ----------


def test_infer_doc_type_declared_wins() -> None:
    assert infer_doc_type("随手拍.jpg", declared="invoice") == "invoice"


def test_infer_doc_type_keywords() -> None:
    assert infer_doc_type("医疗发票_15800.jpg") == "invoice"
    assert infer_doc_type("诊断证明.pdf") == "diagnosis"
    assert infer_doc_type("费用清单.pdf") == "cost_list"
    assert infer_doc_type("出院病历.docx") == "medical_record"
    assert infer_doc_type("pathology_report.pdf") == "medical_record"


def test_infer_doc_type_unknown_returns_none() -> None:
    assert infer_doc_type("photo_2026.jpg") is None
    assert infer_doc_type("", None) is None


# ---------- 金额交叉核验 ----------


def test_contradiction_none_when_amounts_match() -> None:
    docs = [_doc("invoice", "15800.00"), _doc("cost_list", "15800.00")]
    assert find_amount_contradictions(docs) == []


def test_contradiction_detected_on_mismatch() -> None:
    docs = [_doc("invoice", "15800.00"), _doc("cost_list", "12800.00")]
    result = find_amount_contradictions(docs)
    assert len(result) == 1
    assert "15800" in result[0] and "12800" in result[0]


def test_contradiction_skipped_when_amount_missing() -> None:
    docs = [_doc("invoice", None), _doc("cost_list", "12800.00")]
    assert find_amount_contradictions(docs) == []
    # 单边材料也不核
    assert find_amount_contradictions([_doc("invoice", "15800.00")]) == []
