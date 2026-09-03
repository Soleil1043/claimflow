"""材料审核节点（F04）——T079 桩版。

桩语义（确定性，无 LLM/OCR；T082 真实化为 OCR+抽取子图）：
- 必备材料清单按 doc_type 判断完整性（医疗险：发票/诊断证明/费用清单）
- 材料条目带 note 视为已知异常标记（如"发票与清单金额矛盾"）→ 置信度降至阈值下
- 抽取字段留空，仅保留文件级引用
"""

from __future__ import annotations

from typing import Any

from schemas.stages import ExtractedDocument, MaterialReviewOutput
from services.case_store import CaseRecorder
from state import ClaimCaseState

# 险种必备材料清单（医疗险 pack；新险种 pack 在此扩展）
MEDICAL_REQUIRED_DOCS: dict[str, str] = {
    "invoice": "医疗发票",
    "diagnosis": "诊断证明",
    "cost_list": "费用清单",
}


def make_material_review_node(recorder: CaseRecorder):
    """材料审核节点工厂。"""

    async def material_review_node(state: ClaimCaseState) -> dict[str, Any]:
        materials = state.get("materials") or []
        present = {
            m.get("doc_type") for m in materials if m.get("doc_type")
        }
        missing = [label for code, label in MEDICAL_REQUIRED_DOCS.items() if code not in present]
        # 已知异常标记（种子/上传元数据）→ 低置信，交 orchestrator 裁量
        has_anomaly_note = any(m.get("note") for m in materials)

        documents = [
            ExtractedDocument(
                doc_type="invoice",  # 占位：doc_type 未知时 schema 需要合法值
                file_name=str(m.get("file_name", "")),
                confidence=0.4 if has_anomaly_note else 1.0,
            ).model_dump(mode="json")
            for m in materials
        ]
        # 修正占位 doc_type（材料自带则透传）
        for doc, m in zip(documents, materials, strict=False):
            if m.get("doc_type"):
                doc["doc_type"] = m["doc_type"]

        output = MaterialReviewOutput(
            documents=documents,
            completeness="partial" if missing else "complete",
            missing=missing,
            confidence=0.4 if has_anomaly_note else 1.0,
        ).model_dump(mode="json")

        await recorder.event(
            state["case_id"],
            "stage_result",
            stage="material_review",
            payload=output,
        )
        return {"material": output}

    return material_review_node
