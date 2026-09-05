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
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_case_graph, get_db_session
from app.core.logging import get_logger
from schemas.api import (
    CaseInterventionHuman,
    CaseInterventionItem,
    CaseInterventionListResponse,
    CaseResolveRequest,
    CaseResolveResponse,
)
from schemas.case import PENDING_CASE_STATUSES, CaseStatus
from services.case_service import (
    build_agent_resolution,
    build_supplement_resolution,
    decision_doc_payload,
)
from services.db.models import Case
from services.observability.token_tracker import track_case

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
            state = case_graph.get_state(
                {"configurable": {"thread_id": case.id}}
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

    state_values = case_graph.get_state(
        {"configurable": {"thread_id": case_id}}
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

    with track_case(case_id):  # 案件维度 token 归集（T099 CASE_TOKENS）
        result = await case_graph.ainvoke(
            Command(resume=resolution),
            config={"configurable": {"thread_id": case_id}, "recursion_limit": 60},
        )
    await session.refresh(case)

    doc = result.get("decision_document") if isinstance(result, dict) else None
    return CaseResolveResponse(
        case_id=case.id,
        status=case.status,
        final_decision=case.final_decision,
        approved_amount=case.approved_amount,
        decision_document=decision_doc_payload(doc if isinstance(doc, dict) else None),
    )
