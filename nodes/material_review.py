"""材料审核节点（F04，T082 真实化；T116 提取子任务化）。

三段流水线：
1. 逐份材料提取（@task 子任务化并行，D052-2）——优先级：storage_path 真实文件
   （T049 两段式：图片 vision/PDF 文本+扫描件渲染/Word 文本）→ 已存提取结果
   （B03 上传时落档）→ 引用型兜底（种子/测试：doc_type 已知、无字段，
   source=mock_fallback）。任务结果随 checkpoint 持久化：崩溃恢复跳过已提取份数。
2. 规则层（零 LLM，tools/document）——完整性校验（险种清单）+ 金额交叉核验
   （发票 vs 清单，不一致→置信度压至 0.4 交 orchestrator 裁量）
3. AI 一致性审查（可选，llm_router 同款参数化注入）——装载
   skills/material_review/<险种>.md 的审核规程，结构化输出异常清单（fail-open：
   失败仅日志，不影响规则结论）

置信度口径：per-doc 来源置信（vision/text_model=0.9，mock_fallback=0.3，引用型 1.0，
带 note 异常标记 0.4）取最小值，矛盾/AI 异常再压至 0.4——低于
material_confidence_floor（默认 0.6）时由 orchestrator 转人工裁量。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.func import task as graph_task
from langgraph.types import RetryPolicy
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from schemas.stages import ExtractedDocument, MaterialReviewOutput
from services.case_store import CaseRecorder
from services.llm.client import get_chat_model
from services.llm.prompts import MATERIAL_REVIEW_AI_PROMPT
from services.materials import extract_material
from services.skills import build_system_prompt
from state import ClaimCaseState
from tools.document.classify import find_amount_contradictions, infer_doc_type
from tools.document.completeness import validate_completeness

log = get_logger(__name__)

# 来源基准分（与 T049 提取服务 source 口径对齐）
_CONFIDENCE_BASE = {"vision": 0.9, "text_model": 0.9, "mock_fallback": 0.3}
# 已知异常标记（材料条目 note 字段）的置信度
_ANOMALY_CONFIDENCE = 0.4
# 交叉核验/AI 异常命中时的置信度（低于 material_confidence_floor=0.6 → orchestrator 裁量）
_CONTRADICTION_CONFIDENCE = 0.4

# 关键字段缺失扣分（T140 校准，D060）：金额/诊断为核赔核心字段各 -0.25，
# 日期/姓名 -0.1。缺两项核心 = 0.9-0.5 = 0.4，低于 material_confidence_floor
# 0.6 → 转人工裁量——字段缺失真正影响调度（旧口径来源常数同源同分，缺失零感知）
_FIELD_PENALTIES: tuple[tuple[str, float], ...] = (
    ("amount", 0.25),
    ("diagnosis", 0.25),
    ("date", 0.1),
    ("patient_name", 0.1),
)


def _field_missing(value: Any) -> bool:
    """字段缺失判定：None / 空串 / 0 值（0 元发票金额视同未提取）。"""
    return value in (None, "", 0, "0")


def _calibrated_confidence(source: str, extraction: dict[str, Any]) -> float:
    """提取置信度（T140 校准，D060）：来源基准 − 关键字段缺失扣分，clamp [0.05, 基准]。

    确定性校准函数——不做模型自评（不可控不可测，面试缺口#9 口径是"标定常数"）。
    """
    base = _CONFIDENCE_BASE.get(source, 0.5)
    penalty = sum(p for field, p in _FIELD_PENALTIES if _field_missing(extraction.get(field)))
    return round(max(0.05, base - penalty), 2)

# AI 一致性审查的结构化输出
class MaterialAiReview(BaseModel):
    """AI 材料一致性审查结论。"""

    anomalies: list[str] = Field(default_factory=list)
    notes: str = ""


async def _extract_from_file(entry: dict[str, Any]) -> ExtractedDocument:
    """storage_path 真实文件 → T049 两段式提取（图片 vision / PDF / Word）。"""
    path = Path(str(entry.get("storage_path")))
    filename = str(entry.get("file_name") or path.name)
    content = path.read_bytes()
    result = await extract_material(filename, _guess_mime(filename), content)
    return _to_document(filename, entry, result.source, result)


def _guess_mime(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        return "image/png"
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _to_document(
    filename: str,
    entry: dict[str, Any],
    source: str,
    extraction: Any = None,
) -> ExtractedDocument:
    """提取结果（services.materials.MaterialExtraction 或已落档 dict）→ ExtractedDocument。"""
    if extraction is not None and not isinstance(extraction, dict):
        extraction = extraction.model_dump()
    extraction = extraction or {}
    treatment_date: dt.date | None = None
    raw_date = extraction.get("date")
    if raw_date:
        try:
            treatment_date = dt.date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            treatment_date = None
    amount = extraction.get("amount")
    return ExtractedDocument(
        doc_type=str(entry.get("doc_type") or "medical_record"),
        file_name=filename,
        patient_name=extraction.get("patient_name"),
        diagnosis=extraction.get("diagnosis"),
        total_amount=Decimal(str(amount)) if amount not in (None, "") else None,
        treatment_date=treatment_date,
        confidence=_calibrated_confidence(source, extraction),
        source=source,  # type: ignore[arg-type]
    )


def _reference_document(entry: dict[str, Any]) -> ExtractedDocument:
    """引用型材料（无文件无提取，种子/测试口径）：doc_type 已知、无字段。"""
    doc_type = entry.get("doc_type")
    if not doc_type:
        doc_type = infer_doc_type(str(entry.get("file_name", ""))) or "medical_record"
    confidence = _ANOMALY_CONFIDENCE if entry.get("note") else 1.0
    return ExtractedDocument(
        doc_type=str(doc_type),  # type: ignore[arg-type]
        file_name=str(entry.get("file_name", "")),
        confidence=confidence,
        source="mock_fallback",
    )


# 单份提取的声明式容错（D052-2，仅 async task 支持 timeout）：
# RetryPolicy 默认口径——连接类/未知瞬时异常重试，ValueError/OSError 等确定性错误不重试；
# ChatOpenAI 内层自带 max_retries=1，此处再给 1 次尝试（共 2+1 次），120s/次覆盖 vision 长尾。
_EXTRACT_RETRY = RetryPolicy(max_attempts=2)
_EXTRACT_TIMEOUT_S = 120.0


@graph_task(retry_policy=_EXTRACT_RETRY, timeout=_EXTRACT_TIMEOUT_S)
async def _extract_one(entry: dict[str, Any]) -> ExtractedDocument:
    """单份材料提取（@task 子任务化，D052-2）：真实文件 → 已落档提取结果 → 引用型兜底。

    任务结果随图 checkpoint 持久化——进程崩溃恢复时已完成的份数不重付 LLM 调用；
    调用顺序须稳定（resume 按调用序匹配缓存结果），材料列表只追加不重排。
    """
    storage_path = entry.get("storage_path")
    if storage_path and Path(str(storage_path)).is_file():
        return await _extract_from_file(entry)
    if entry.get("extraction"):
        extraction = entry["extraction"]
        # source 取自提取结果本身（vision/text_model 决定置信度）
        source = str(extraction.get("source") or "mock_fallback")
        return _to_document(
            str(entry.get("file_name", "")), entry, source, extraction
        )
    return _reference_document(entry)


def make_material_ai_reviewer():
    """AI 一致性审查器工厂（默认；settings.material_review_llm_enabled=False → None）。"""
    if not settings.material_review_llm_enabled:
        return None

    async def ai_reviewer(state: ClaimCaseState, documents: list[dict[str, Any]]) -> list[str]:
        line = state.get("case_type") or "_shared"
        system = build_system_prompt(
            MATERIAL_REVIEW_AI_PROMPT, "material_review", line,
            documents=json.dumps(documents, ensure_ascii=False, default=str)[:2500],
        )
        model = get_chat_model(temperature=0.0)
        structured = model.with_structured_output(MaterialAiReview, method="function_calling")
        from services.observability.token_tracker import phase_ainvoke

        # T164：接入 observed_ainvoke（缓存/用量按 stage=material_review 分列）
        review = await phase_ainvoke(
            structured, [HumanMessage(content=system)], phase="material_review"
        )
        return review.anomalies

    return ai_reviewer


def make_material_review_node(recorder: CaseRecorder, ai_reviewer=None):
    """材料审核节点工厂。ai_reviewer=None 时仅规则层（测试零 LLM）。"""

    async def material_review_node(state: ClaimCaseState) -> dict[str, Any]:
        line = str(state.get("case_type") or "unknown")  # 未上线险种不经此处（intake 守卫）
        entries = state.get("materials") or []

        # 并行提取（D052-2）：@task 返回 future，先全部启动再按序收集——
        # 多份材料并发走 LLM 提取，输出顺序与材料列表一致
        futures = [_extract_one(entry) for entry in entries]
        documents: list[ExtractedDocument] = []
        for entry, fut in zip(entries, futures, strict=True):
            try:
                documents.append(await fut)
            # 单份提取失败不阻塞（fail-open，D008 语义；重试已由 @task retry_policy 承载）
            except Exception as exc:  # noqa: BLE001
                log.warning("material_extract_failed",
                            case_id=state["case_id"],
                            file=str(entry.get("file_name")), error=str(exc)[:200])
                documents.append(_reference_document(entry))

        docs_dump = [d.model_dump(mode="json") for d in documents]

        # 规则层：完整性（险种清单）+ 金额交叉核验
        completeness_result = validate_completeness(docs_dump, line)
        contradictions = find_amount_contradictions(docs_dump)

        # AI 一致性审查（可选，skill 装配；fail-open）
        anomalies: list[str] = []
        if ai_reviewer is not None:
            try:
                anomalies = await ai_reviewer(state, docs_dump)
            except Exception as exc:  # noqa: BLE001
                log.warning("material_ai_review_failed",
                            case_id=state["case_id"], error=str(exc)[:200])

        confidence = min((d.confidence for d in documents), default=1.0)
        if contradictions or anomalies:
            confidence = min(confidence, _CONTRADICTION_CONFIDENCE)

        output = MaterialReviewOutput(
            documents=documents,
            completeness=completeness_result["completeness"],  # type: ignore[arg-type]
            missing=completeness_result["missing"],
            confidence=round(confidence, 2),
        ).model_dump(mode="json")

        await recorder.event(
            state["case_id"],
            "stage_result",
            stage="material_review",
            payload={**output,
                     "contradictions": contradictions,
                     "ai_anomalies": anomalies},
        )
        return {"material": output}

    return material_review_node
