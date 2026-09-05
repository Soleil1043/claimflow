"""核赔案件工单路由（T086/T087，F12）。

- GET  /api/v1/interventions/cases                       核赔工单列表（interrupt 挂起案件）
- POST /api/v1/interventions/cases/{case_id}/resolve     处理工单（Command(resume) 恢复流程）

工单类型与恢复语义（nodes/human_gate.py）：
- supplement：补传材料 → 重跑材料审核 → 回 orchestrator
- review：confirm 签发 / rewrite 改判（坐席文本过红线复审，违规不签发）
- escape：转专家线下，终态 referred
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_case_dispatcher, get_case_graph, get_db_session
from app.core.logging import get_logger
from schemas.api import (
    CaseInterventionHuman,
    CaseInterventionItem,
    CaseInterventionListResponse,
    CaseResolveRequest,
    CaseResolveResponse,
)
from schemas.case import PENDING_CASE_STATUSES, CaseStatus
from services.case_jobs import CaseJobConflictError, JobAction, enqueue_case_job, job_envelope
from services.case_service import (
    build_agent_resolution,
    build_supplement_resolution,
    decision_doc_payload,
    decision_doc_view,
)
from services.db.models import Case

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/interventions", tags=["interventions"])


@router.get("/cases", response_model=CaseInterventionListResponse)
async def list_case_interventions(
    status: str | None = Query(default=None, description="按案件状态筛选"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    case_graph=Depends(get_case_graph),  # noqa: B008
) -> CaseInterventionListResponse:
    """核赔工单列表：interrupt 挂起的案件（补件/复核签批/受理升级）。"""
    stmt = (
        select(Case)
        .where(Case.status.in_(PENDING_CASE_STATUSES))
        .order_by(Case.updated_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Case.status == status)
    rows = (await session.execute(stmt)).scalars().all()

    items: list[CaseInterventionItem] = []
    for case in rows:
        human = CaseInterventionHuman(kind="review")
        try:
            state = (
                await case_graph.aget_state({"configurable": {"thread_id": case.id}})
            ).values
            request = state.get("human_request") or {}
            human = CaseInterventionHuman(
                kind=str(request.get("kind", "review")),
                reason=request.get("reason"),
                missing=list(request.get("missing", []) or []),
            )
        except Exception as exc:  # noqa: BLE001——checkpoint 不可读时降级展示
            log.warning("case_state_read_failed", case_id=case.id, error=str(exc)[:120])
        items.append(
            CaseInterventionItem(
                case_id=case.id,
                case_type=case.case_type,
                status=case.status,
                claimed_amount=case.claimed_amount,
                human=human,
                created_at=case.created_at,
            )
        )
    return CaseInterventionListResponse(total=len(items), items=items)


@router.post("/cases/{case_id}/resolve", response_model=CaseResolveResponse)
async def resolve_case_intervention(
    case_id: str,
    body: CaseResolveRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    case_graph=Depends(get_case_graph),  # noqa: B008
    dispatcher=Depends(get_case_dispatcher),  # noqa: B008
) -> CaseResolveResponse:
    """处理核赔工单：以 Command(resume=...) 恢复挂起的核赔流程。

    - supplement：补传材料 → 重跑材料审核 → 回 orchestrator（也可经 B03 上传自动触发）
    - review：confirm 签发 / rewrite 改判（坐席文本过红线复审，违规不签发）
    - escape：转专家线下，终态 referred
    """
    case = (
        await session.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    if case.status not in PENDING_CASE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"案件状态为 {case.status}，无待处理工单",
        )

    state_values = (
        await case_graph.aget_state({"configurable": {"thread_id": case_id}})
    ).values
    kind = str((state_values.get("human_request") or {}).get("kind")
               or ("supplement" if case.status == CaseStatus.SUPPLEMENT_PENDING else "review"))

    if kind == "supplement":
        resolution = build_supplement_resolution(
            [m.model_dump() for m in body.added_materials or []],
            resolved_by=body.resolved_by,
        )
    else:
        resolution = build_agent_resolution(
            kind=kind,
            action=body.action,
            decision=body.decision,
            approved_amount=body.approved_amount,
            reason=body.reason,
            body=body.body,
            note=body.note,
            resolved_by=body.resolved_by,
        )

    # 交付任务行（T103）：恢复与裁决载荷同事务落库；在飞冲突 → 409（防并发双签）
    try:
        job = await enqueue_case_job(
            session, case_id=case_id, action=JobAction.RESUME, payload=resolution
        )
    except CaseJobConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该案件已有处理中的交付任务，请稍候",
        ) from None
    await session.commit()

    await dispatcher.dispatch(job.id)

    await session.refresh(case)
    await session.refresh(job)
    # 决定书读模型（D046 单源，与 cases 端点同口径）：(最新版, 是否已签发)
    doc, doc_issued = await decision_doc_view(session, case)
    return CaseResolveResponse(
        case_id=case.id,
        status=case.status,
        final_decision=case.final_decision,
        approved_amount=case.approved_amount,
        decision_document=decision_doc_payload(doc),
        decision_issued=doc_issued,
        job=job_envelope(job),
    )
