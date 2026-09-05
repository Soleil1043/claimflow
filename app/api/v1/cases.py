"""核赔案件路由（B01-B03，Phase 8 T080）。

- B01 POST /api/v1/cases                      提交案件（交付队列异步执行，T103；自然键幂等）
- B02 GET  /api/v1/cases/{case_id}            案件详情（进度/结论/决定书/审计时间线）
- B03 POST /api/v1/cases/{case_id}/materials  上传材料（复用 T049 提取服务，落案件档案）

幂等口径（F01）：自然键 = (user_id, policy_no, claimed_amount, incident_date)——
重复提交返回既有案件（200 + idempotent=true），不重复执行核赔。
转人工挂起（interrupt）：任务成功终态（outcome=interrupted + 回执快照，T103），
human 字段来自回执而非 checkpoint；恢复通道见 T086（interventions）。
background 档（默认）：POST 受理即返回，终态经 GET /cases/{id} 轮询（job 字段跟踪交付）。
"""

from __future__ import annotations

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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_case_dispatcher, get_db_session
from app.core.config import settings
from app.core.logging import get_logger
from schemas.api import (
    CaseCreateRequest,
    CaseDetailResponse,
    CaseHumanInfo,
    CaseMaterialUploadResponse,
    CaseSubmitResponse,
    CaseTimelineEvent,
)
from schemas.case import CaseStatus
from schemas.lines import all_doc_types
from services.case_jobs import (
    CaseJobConflictError,
    JobAction,
    enqueue_case_job,
    job_envelope,
    latest_job,
)
from services.case_service import (
    build_supplement_resolution,
    decision_doc_payload,
    decision_doc_view,
    find_idempotent_case,
    generate_case_id,
    new_case,
)
from services.case_store import get_default_recorder
from services.db.models import Case, CaseEvent, CaseJob
from services.materials import detect_material_type, extract_material
from services.memory.case_memory import search_case_memories

# 指标与 token 归集已随交付执行体迁 services.case_jobs（T103）

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


def _human_from_job(job: CaseJob | None) -> CaseHumanInfo | None:
    """任务回执 → 挂起信息（免读 checkpoint 的投影，T103）。"""
    if job is None or job.outcome != "interrupted":
        return None
    payload = job.interrupt_payload or {}
    return CaseHumanInfo(
        kind=str(payload.get("kind", "review")),
        reason=payload.get("reason"),
        missing=list(payload.get("missing", []) or []),
    )


async def _get_case_or_404(case_id: str, session: AsyncSession) -> Case:
    case = (
        await session.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    return case


@router.post("", response_model=CaseSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_case(
    body: CaseCreateRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    dispatcher=Depends(get_case_dispatcher),  # noqa: B008
) -> CaseSubmitResponse:
    """B01 提交案件：自然键幂等 → 建档 → 任务行同事务落库 → 派发执行（T103）。

    新建 201（受理快照 + job 交付凭证）；幂等命中 200（既有终态）。
    """
    # 自然键幂等（F01，service 收口）：重复提交返回既有案件，不重复执行
    existing = await find_idempotent_case(
        session,
        user_id=body.user_id,
        policy_no=body.policy_no,
        claimed_amount=body.claimed_amount,
        incident_date=body.incident_date,
    )
    if existing is not None:
        log.info("case_submit_idempotent_hit", case_id=existing.id)
        response.status_code = status.HTTP_200_OK
        existing_doc, existing_issued = await decision_doc_view(session, existing)
        return CaseSubmitResponse(
            case_id=existing.id,
            case_type=existing.case_type,
            status=existing.status,
            final_decision=existing.final_decision,
            approved_amount=existing.approved_amount,
            decision_document=decision_doc_payload(existing_doc),
            decision_issued=existing_issued,
            human=None,
            idempotent=True,
        )

    case = new_case(
        case_id=await generate_case_id(session),
        user_id=body.user_id,
        policy_no=body.policy_no,
        claimed_amount=body.claimed_amount,
        incident_date=body.incident_date,
        incident_description=body.incident_description,
        materials=[m.model_dump() for m in body.materials],
    )
    session.add(case)
    # 交付任务行与建档同事务（outbox，T103）：提交成功但任务丢失在设计上不可能
    job = await enqueue_case_job(
        session,
        case_id=case.id,
        action=JobAction.RUN,
        payload={
            "case_id": case.id,
            "user_id": body.user_id,
            "policy_id": body.policy_no,
            "claimed_amount": body.claimed_amount,
            "incident_date": body.incident_date,
            "incident_description": body.incident_description,
            "declared_case_type": body.declared_case_type,
            "materials": [m.model_dump() for m in body.materials],
        },
    )
    # 显式提交：图内节点经独立会话（CaseRecorder）更新本行，先落基线避免锁等待
    await session.commit()

    await dispatcher.dispatch(job.id)

    # 执行体（inline 同请求 / background 循环）经独立会话更新行——重读权威快照
    await session.refresh(case)
    await session.refresh(job)
    doc, doc_issued = await decision_doc_view(session, case)

    return CaseSubmitResponse(
        case_id=case.id,
        case_type=case.case_type,
        status=case.status,
        final_decision=case.final_decision,
        approved_amount=case.approved_amount,
        decision_document=decision_doc_payload(doc),
        decision_issued=doc_issued,
        human=_human_from_job(job),
        job=job_envelope(job),
    )


@router.get("/{case_id}", response_model=CaseDetailResponse)
async def get_case(
    case_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CaseDetailResponse:
    """B02 案件详情：进度（状态）/ 结论 / 决定书 / 审计时间线（seq 升序）。"""
    case = await _get_case_or_404(case_id, session)

    # 申请人历史核赔档案（T100）：终态记忆语义检索，排除本案件；fail-open 空列表兜底
    memories = await search_case_memories(str(case.user_id), exclude_case_id=case.id)

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
    job = await latest_job(case_id)
    doc, doc_issued = await decision_doc_view(session, case)
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
        decision_document=decision_doc_payload(doc),
        decision_issued=doc_issued,
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
        applicant_memories=[m.model_dump() for m in memories],
        job=job_envelope(job) if job is not None else None,
        human=_human_from_job(job),
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
    dispatcher=Depends(get_case_dispatcher),  # noqa: B008
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

    if doc_type is not None and doc_type not in all_doc_types():
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
    # 审计事件经共享 recorder 单例（T095）：与图内节点同一 seq 分配器，杜绝重号
    await get_default_recorder().event(
        case.id,
        "material_upload",
        payload={
            "file_name": filename,
            "file_type": result.file_type,
            "doc_type": doc_type,
            "source": result.source,
        },
    )
    await session.commit()  # 材料/审计先原子落库（上传事实不因恢复状态丢失）

    # 补件闭环（T086/T103）：挂起案件 + 新材料 → resume 任务行（独立事务——
    # enqueue 撞活跃唯一约束会毒化会话，材料已在前一事务持久化，仅回滚任务行）；
    # 上一次恢复仍在飞是唯一冲突源，恢复语义本就幂等
    resume_job = None
    if case.status == CaseStatus.SUPPLEMENT_PENDING:
        try:
            resume_job = await enqueue_case_job(
                session,
                case_id=case.id,
                action=JobAction.RESUME,
                payload=build_supplement_resolution(
                    [materials[-1]], resolved_by="customer_upload"
                ),
            )
            await session.commit()
        except CaseJobConflictError:
            await session.rollback()
            log.info("supplement_resume_inflight", case_id=case.id)

    if resume_job is not None:
        try:
            await dispatcher.dispatch(resume_job.id)
            await session.refresh(case)
            await session.refresh(resume_job)
        except Exception as exc:  # noqa: BLE001——恢复失败不阻塞上传（任务行有重试）
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
        case_status=case.status if resume_job is not None else None,
        job=job_envelope(resume_job) if resume_job is not None else None,
    )
