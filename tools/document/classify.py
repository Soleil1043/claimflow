"""材料分类与交叉核验（F04/T082）：doc_type 推断、金额一致性检测。

纯函数规则（零 LLM）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# 文件名关键词 → doc_type（推断顺序即优先级）
_DOC_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("发票", "invoice"),
    ("invoice", "invoice"),
    ("诊断", "diagnosis"),
    ("diagnosis", "diagnosis"),
    ("清单", "cost_list"),
    ("病历", "medical_record"),
    ("记录", "medical_record"),
    ("病理", "medical_record"),
    ("pathology", "medical_record"),
)

# 交叉核验的金额对：两组 doc_type 的 total_amount 应一致
_AMOUNT_CROSS_CHECK_PAIRS: tuple[tuple[str, str, str], ...] = (
    # (doc_type_a, doc_type_b, 不一致描述模板)
    ("invoice", "cost_list", "发票金额与费用清单金额不一致"),
)


def infer_doc_type(filename: str, declared: str | None = None) -> str | None:
    """推断材料 doc_type：声明值优先，否则文件名关键词，无法识别返回 None。"""
    if declared:
        return declared
    lowered = (filename or "").lower()
    for keyword, doc_type in _DOC_TYPE_KEYWORDS:
        if keyword in lowered:
            return doc_type
    return None


def _amount(doc: dict[str, Any]) -> Decimal | None:
    value = doc.get("total_amount")
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def find_amount_contradictions(documents: list[dict[str, Any]]) -> list[str]:
    """交叉核验材料间金额一致性；返回矛盾描述列表（空=无矛盾）。

    仅当两组材料的金额都提取到且不相等时报告（容差 0.00，精确比较——金额一分不差
    是理赔材料的基本要求）。
    """
    by_type = {
        d.get("doc_type"): d
        for d in documents
        if isinstance(d, dict) and d.get("doc_type")
    }
    contradictions: list[str] = []
    for type_a, type_b, message in _AMOUNT_CROSS_CHECK_PAIRS:
        doc_a, doc_b = by_type.get(type_a), by_type.get(type_b)
        if doc_a is None or doc_b is None:
            continue
        amount_a, amount_b = _amount(doc_a), _amount(doc_b)
        if amount_a is None or amount_b is None:
            continue
        if amount_a != amount_b:
            contradictions.append(f"{message}（{amount_a} ≠ {amount_b}）")
    return contradictions
