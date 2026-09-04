"""核赔案件路由（B01-B03，Phase 8 T080）。

- B01 POST /api/v1/cases                      提交案件（同步驱动核赔主图；自然键幂等）
- B02 GET  /api/v1/cases/{case_id}            案件详情（进度/结论/决定书/审计时间线）
- B03 POST /api/v1/cases/{case_id}/materials  上传材料（复用 T049 提取服务，落案件档案）

幂等口径（F01）：自然键 = (user_id, policy_no, claimed_amount, incident_date)——
重复提交返回既有案件（200 + idempotent=true），不重复执行核赔。
转人工挂起（interrupt）：图返回 __interrupt__ 时案件已由节点落库为
supplement_pending / referred，响应携带 human 信息；恢复通道见 T086（interventions）。
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_case_graph, get_db_session
from app.core.config import settings
from app.core.logging import get_logger
from schemas.api import (
    CaseCreateRequest,
    CaseDecisionDocumentOut,
    CaseDetailResponse,
    CaseHumanInfo,
    CaseMaterialUploadResponse,
    CaseSubmitResponse,
    CaseTimelineEvent,
)
from services.db.models import Case, CaseEvent, DecisionDocument
from services.materials import detect_material_type, extract_material
from services.observability import metrics as obs

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


async def _get_case_or_404(case_id: str, session: AsyncSession) -> Case:
    case = (
        await session.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    return case


async def _next_case_id(session: AsyncSession) -> str:
    """按年生成业务案件号 CASE-YYYY-NNNN（PoC 口径：当年计数 + 1，撞号重试）。"""
    year = dt.date.today().year
    for _ in range(5):
        count = (
            await session.execute(
                select(func.count(Case.id)).where(Case.id.like(f"CASE-{year}-%"))
            )
        ).scalar_one()
        candidate = f"CASE-{year}-{count + 1:04d}"
        exists = (
            await session.execute(select(Case.id).where(Case.id == candidate))
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    msg = "案件号生成失败"
    raise RuntimeError(msg)


def _decision_doc_out(doc: DecisionDocument | None) -> CaseDecisionDocumentOut | None:
    if doc is None:
        return None
    return CaseDecisionDocumentOut(
        version=doc.version,
        title=doc.title,
        conclusion=doc.conclusion,
        approved_amount=doc.approved_amount,
        issued_by=doc.issued_by,
        body=doc.body,
    )


async def _latest_decision_doc(
    case_id: str, session: AsyncSession
) -> DecisionDocument | None:
    return (
        await session.execute(
            select(DecisionDocument)
            .where(DecisionDocument.case_id == case_id)
            .order_by(DecisionDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.post("", response_model=CaseSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_case(
    body: CaseCreateRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    case_graph=Depends(get_case_graph),  # noqa: B008
) -> CaseSubmitResponse:
    """B01 提交案件：自然键幂等 → 建档 → 同步驱动核赔主图。

    新建 201；幂等命中 200。自动签发：终态结论/决定书；转人工/补件：挂起状态与
    human 信息（恢复经 interventions，T086）。
    """
    # 自然键幂等（F01）：重复提交返回既有案件，不重复执行
    existing = (
        await session.execute(
            select(Case).where(
                Case.user_id == body.user_id,
                Case.policy_no == body.policy_no,
                Case.claimed_amount == body.claimed_amount,
                Case.incident_date == body.incident_date,
            )
        )
    ).scalars().first()
    if existing is not None:
        log.info("case_submit_idempotent_hit", case_id=existing.id)
        response.status_code = status.HTTP_200_OK
        return CaseSubmitResponse(
            case_id=existing.id,
            case_type=existing.case_type,
            status=existing.status,
            final_decision=existing.final_decision,
            approved_amount=existing.approved_amount,
            decision_document=_decision_doc_out(
                await _latest_decision_doc(existing.id, session)
            ),
            human=None,
            idempotent=True,
        )

    case = Case(
        id=await _next_case_id(session),
        user_id=body.user_id,
        policy_no=body.policy_no,
        case_type="unknown",  # intake 分类写入
        status="received",
        claimed_amount=body.claimed_amount,
        incident_date=body.incident_date,
        incident_description=body.incident_description,
        materials=[m.model_dump() for m in body.materials],
    )
    session.add(case)
    # 显式提交：图内节点经独立会话（CaseRecorder）更新本行，先落基线避免锁等待
    await session.commit()

    started = time.monotonic()
    result = await case_graph.ainvoke(
        {
            "case_id": case.id,
            "user_id": body.user_id,
            "policy_id": body.policy_no,
            "claimed_amount": body.claimed_amount,
            "incident_date": body.incident_date,
            "incident_description": body.incident_description,
            "declared_case_type": body.declared_case_type,
            "materials": [m.model_dump() for m in body.materials],
        },
        config={"configurable": {"thread_id": case.id}, "recursion_limit": 60},
    )

    obs.record_case_duration(time.monotonic() - started)

    # 图内节点经独立会话更新了状态——重读权威行
    await session.refresh(case)

    human: CaseHumanInfo | None = None
    interrupt_payloads = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupt_payloads:
        payload = interrupt_payloads[0].value
        human = CaseHumanInfo(
            kind=str(payload.get("kind", "review")),
            reason=payload.get("reason"),
            missing=list(payload.get("missing", []) or []),
        )

    return CaseSubmitResponse(
        case_id=case.id,
        case_type=case.case_type,
        status=case.status,
        final_decision=case.final_decision,
        approved_amount=case.approved_amount,
        decision_document=_decision_doc_out(await _latest_decision_doc(case.id, session)),
        human=human,
    )


@router.get("/{case_id}", response_model=CaseDetailResponse)
async def get_case(
    case_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CaseDetailResponse:
    """B02 案件详情：进度（状态）/ 结论 / 决定书 / 审计时间线（seq 升序）。"""
    case = await _get_case_or_404(case_id, session)

    events = (
        (
            await session.execute(
                select(CaseEvent)
                .where(CaseEvent.case_id == case_id)
                .order_by(CaseEvent.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    return CaseDetailResponse(
        case_id=case.id,
        user_id=case.user_id,
        policy_no=case.policy_no,
        case_type=case.case_type,
        status=case.status,
        claimed_amount=case.claimed_amount,
        approved_amount=case.approved_amount,
        final_decision=case.final_decision,
        materials=list(case.materials or []),
        decision_document=_decision_doc_out(
            await _latest_decision_doc(case_id, session)
        ),
        timeline=[
            CaseTimelineEvent(
                seq=e.seq,
                kind=e.kind,
                stage=e.stage,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in events
        ],
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


@router.post("/{case_id}/materials", response_model=CaseMaterialUploadResponse)
async def upload_case_material(
    case_id: str,
    file: UploadFile = File(...),  # noqa: B008
    doc_type: str | None = Form(  # noqa: B008
        default=None,
        description="材料类型：invoice/diagnosis/cost_list/medical_record（可空）",
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    case_graph=Depends(get_case_graph),  # noqa: B008
) -> CaseMaterialUploadResponse:
    """B03 上传材料：复用 T049 两段式提取（图片/PDF/Word），提取结果落案件档案与审计。

    补件闭环（T086）：案件处于补件挂起时，上传新材料后自动 Command(resume) 恢复
    核赔流程（材料审核重跑 → 回 orchestrator 重规划）。
    """
    case = await _get_case_or_404(case_id, session)

    filename = file.filename or ""
    if filename.lower().endswith(".doc"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="暂不支持 .doc 旧格式，请另存为 .docx 后重新上传",
        )
    file_type = detect_material_type(filename, file.content_type or "")
    if not file_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="仅支持图片（png/jpeg/webp/bmp）、PDF、Word(.docx) 文件",
        )
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="上传文件为空"
        )
    if len(content) > settings.material_max_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"文件超过大小上限（{settings.material_max_size_mb}MB）",
        )

    result = await extract_material(filename, file.content_type or "", content)

    if doc_type is not None and doc_type not in {
        "invoice",
        "diagnosis",
        "cost_list",
        "medical_record",
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="doc_type 取值非法"
        )

    # 文件落盘（T082）：storage_path 供材料审核真实提取（补件重跑/复核时读取）
    materials_dir = Path(settings.case_materials_dir) / case_id
    materials_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(filename).name}"
    storage_path = materials_dir / safe_name
    storage_path.write_bytes(content)

    materials = list(case.materials or [])
    materials.append(
        {
            "file_name": filename,
            "file_type": result.file_type,
            "doc_type": doc_type,
            "storage_path": str(storage_path),
            "extraction": result.model_dump(),
        }
    )
    case.materials = materials
    case.updated_at = func.now()
    session.add(
        CaseEvent(
            case_id=case.id,
            kind="material_upload",
            stage=None,
            seq=await _next_event_seq(session, case.id),
            payload={
                "file_name": filename,
                "file_type": result.file_type,
                "doc_type": doc_type,
                "source": result.source,
            },
        )
    )
    await session.commit()  # 先落库材料/审计，避免 resume 期间并发写锁（SQLite）

    # 补件闭环（T086）：挂起案件 + 新材料 → 自动恢复核赔流程
    resume_status: str | None = None
    if case.status == "supplement_pending":
        try:
            await case_graph.ainvoke(
                Command(resume={"kind": "supplement",
                                "added_materials": [materials[-1]],
                                "resolved_by": "customer_upload"}),
                config={"configurable": {"thread_id": case.id}, "recursion_limit": 60},
            )
            await session.refresh(case)
            resume_status = case.status
        except Exception as exc:  # noqa: BLE001——恢复失败不阻塞上传（可经工单重试）
            log.warning("supplement_resume_failed", case_id=case.id, error=str(exc)[:200])

    return CaseMaterialUploadResponse(
        case_id=case.id,
        filename=filename,
        file_type=result.file_type,
        doc_type=doc_type,
        source=result.source,
        patient_name=result.patient_name,
        diagnosis=result.diagnosis,
        amount=result.amount,
        date=result.date,
        materials_count=len(materials),
        case_status=resume_status,
    )


async def _next_event_seq(session: AsyncSession, case_id: str) -> int:
    current = (
        await session.execute(
            select(func.max(CaseEvent.seq)).where(CaseEvent.case_id == case_id)
        )
    ).scalar_one_or_none()
    return (current or 0) + 1
