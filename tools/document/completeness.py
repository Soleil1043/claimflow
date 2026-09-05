"""材料完整性校验（F04）：按险种必备材料清单判定 complete/partial。

纯函数规则（零 LLM）；清单由险种 pack 承载（schemas.lines.InsuranceLinePack，
T096）——新险种 = 新增 pack 实例，本模块零改动。
"""

from __future__ import annotations

from typing import Any

from schemas.lines import get_line_pack


def validate_completeness(
    documents: list[dict[str, Any]], line: str
) -> dict[str, Any]:
    """按险种清单校验材料完整性。

    documents：ExtractedDocument dump 列表（至少含 doc_type）。
    返回 {"completeness": "complete"|"partial", "missing": [展示名...]}。
    险种无清单（unknown/未上线）→ 视为 complete（该类案件受理期即转人工，不在此消费）。
    """
    pack = get_line_pack(line)
    required = dict(pack.required_docs) if pack is not None else {}
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
