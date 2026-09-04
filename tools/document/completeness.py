"""材料完整性校验（F04）：按险种必备材料清单判定 complete/partial。

纯函数规则（零 LLM）；清单以 doc_type 编码为键（invoice/diagnosis/cost_list/medical_record），
新险种 pack 在 REQUIRED_DOCS_BY_LINE 扩展。
"""

from __future__ import annotations

from typing import Any

# 险种必备材料清单（键=doc_type，值=对外展示名）
REQUIRED_DOCS_BY_LINE: dict[str, dict[str, str]] = {
    "medical": {
        "invoice": "医疗发票",
        "diagnosis": "诊断证明",
        "cost_list": "费用清单",
    },
    # auto / property / accident pack 二期补充
}


def validate_completeness(
    documents: list[dict[str, Any]], line: str
) -> dict[str, Any]:
    """按险种清单校验材料完整性。

    documents：ExtractedDocument dump 列表（至少含 doc_type）。
    返回 {"completeness": "complete"|"partial", "missing": [展示名...]}。
    险种无清单（unknown/未上线）→ 视为 complete（该类案件受理期即转人工，不在此消费）。
    """
    required = REQUIRED_DOCS_BY_LINE.get(line)
    if not required:
        return {"completeness": "complete", "missing": []}

    present = {
        d.get("doc_type") for d in documents if isinstance(d, dict) and d.get("doc_type")
    }
    missing = [label for code, label in required.items() if code not in present]
    return {
        "completeness": "partial" if missing else "complete",
        "missing": missing,
    }
