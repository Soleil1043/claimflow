"""核赔案件工单路由（T086/T087，F12）。

- GET  /api/v1/interventions/cases                       核赔工单列表（interrupt 挂起案件）
- POST /api/v1/interventions/cases/{case_id}/resolve     处理工单（Command(resume) 恢复流程）
- GET  /api/v1/interventions/narrative-samples           决定书叙述抽评队列（T139）
- POST /api/v1/interventions/cases/{case_id}/narrative-review  叙述评审（pass/revise 落审计）

工单类型与恢复语义（nodes/human_gate.py）：
- supplement：补传材料 → 重跑材料审核 → 回 orchestrator
- review：confirm 签发 / rewrite 改判（坐席文本过红线复审，违规不签发）
- escape：转专家线下，终态 referred
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_case_dispatcher, get_db_session, require_staff
from app.core.logging import get_logger
from schemas.api import (
    CaseHumanInfo,
    CaseInterventionItem,
    CaseInterventionListResponse,
    CaseResolveRequest,
    CaseResolveResponse,
    NarrativeReviewRequest,
    NarrativeReviewResponse,
    NarrativeSampleItem,
    NarrativeSampleListResponse,
    NarrativeSampleStats,
)
from schemas.case import PENDING_CASE_STATUSES
from services.case_jobs import JobAction, deliver_case_job, job_envelope, latest_job
from services.case_service import (
    build_agent_resolution,
    build_supplement_resolution,
    decision_doc_payload,
    decision_doc_view,
    human_info_from_job,
    latest_jobs_for_cases,
)
from services.case_store import get_default_recorder
from services.db.models import Case, CaseEvent, DecisionDocument

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/interventions",
    tags=["interventions"],
    # 本路由全部为坐席端点（T147 鉴权：X-Staff-Key；dev 未配置 Key 时放行）
    dependencies=[Depends(require_staff)],
)


@router.get("/cases", response_model=CaseInterventionListResponse)
async def list_case_interventions(
    status: str | None = Query(default=None, description="按案件状态筛选"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CaseInterventionListResponse:
    """核赔工单列表：interrupt 挂起的案件（补件/复核签批/受理升级）。

    挂起信息单源 = 交付回执（D047）：单 SQL 批量取最新任务行，不再逐案
    读 checkpoint（N+1 网络往返消失）。
    """
    stmt = (
        select(Case)
        .where(Case.status.in_(PENDING_CASE_STATUSES))
        .order_by(Case.updated_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Case.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    jobs = await latest_jobs_for_cases([c.id for c in rows])

    items: list[CaseInterventionItem] = []
    for case in rows:
        info = human_info_from_job(jobs.get(case.id), case.status)
        human = CaseHumanInfo(
            kind=info["kind"], reason=info["reason"], missing=info["missing"]
        )
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
    dispatcher=Depends(get_case_dispatcher),  # noqa: B008
    staff: str | None = Depends(require_staff),
) -> CaseResolveResponse:
    """处理核赔工单：以 Command(resume=...) 恢复挂起的核赔流程。

    - supplement：补传材料 → 重跑材料审核 → 回 orchestrator（也可经 B03 上传自动触发）
    - review：confirm 签发 / rewrite 改判（坐席文本过红线复审，违规不签发）
    - escape：转专家线下，终态 referred

    身份（T147）：staff_keys 已配置时 resolved_by 由 Key 派生（可信），
    未配置（dev）回退请求体自报。
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

    resolved_by = staff or body.resolved_by

    # kind 单源 = 交付回执（D047/D051：保守默认内建 human_info_from_job）
    kind = str(human_info_from_job(await latest_job(case_id), case.status)["kind"])

    if kind == "supplement":
        resolution = build_supplement_resolution(
            [m.model_dump() for m in body.added_materials or []],
            resolved_by=resolved_by,
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
            resolved_by=resolved_by,
        )

    # 交付收口（D049）：恢复载荷同事务落库 → 派发 → 回快照；在飞冲突 → 409（防并发双签）
    job = await deliver_case_job(
        session, case_id=case_id, action=JobAction.RESUME, payload=resolution,
        dispatcher=dispatcher,
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该案件已有处理中的交付任务，请稍候",
        )
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


# ---------- 决定书叙述抽评（T139，缺口#4：人工口径先于 judge） ----------

NARRATIVE_SAMPLE_KIND = "narrative_sample"
NARRATIVE_REVIEW_KIND = "narrative_review"


@router.get("/narrative-samples", response_model=NarrativeSampleListResponse)
async def list_narrative_samples(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> NarrativeSampleListResponse:
    """叙述抽评队列：已采样未评审的自动签发案件 + 通过率统计。

    采样在 auto_adjudicate 签发分支（确定性，种子=case_id）；评审结论落
    narrative_review 事件（kind 自由字符串，时间线消费方有兜底）。
    """
    sample_rows = (
        (
            await session.execute(
                select(CaseEvent)
                .where(CaseEvent.kind == NARRATIVE_SAMPLE_KIND)
                .order_by(CaseEvent.id.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_sample: dict[str, CaseEvent] = {}
    for row in sample_rows:
        latest_sample.setdefault(row.case_id, row)  # id 降序 → 首见即最新

    review_rows = (
        (
            await session.execute(
                select(CaseEvent).where(CaseEvent.kind == NARRATIVE_REVIEW_KIND)
            )
        )
        .scalars()
        .all()
    )
    reviewed: set[str] = {r.case_id for r in review_rows}
    pending_ids = [cid for cid in latest_sample if cid not in reviewed]

    stats = NarrativeSampleStats(
        sampled=len(latest_sample),
        reviewed=len(reviewed),
        passed=sum(1 for r in review_rows if (r.payload or {}).get("verdict") == "pass"),
    )
    stats.revised = stats.reviewed - stats.passed
    if not pending_ids:
        return NarrativeSampleListResponse(total=0, items=[], stats=stats)

    cases = {
        c.id: c
        for c in (
            await session.execute(select(Case).where(Case.id.in_(pending_ids)))
        ).scalars().all()
    }
    docs = (
        (
            await session.execute(
                select(DecisionDocument)
                .where(DecisionDocument.case_id.in_(pending_ids))
                .order_by(DecisionDocument.case_id, DecisionDocument.version.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_doc: dict[str, DecisionDocument] = {}
    for d in docs:
        latest_doc.setdefault(d.case_id, d)

    items = []
    for cid in pending_ids[:limit]:
        case = cases.get(cid)
        doc = latest_doc.get(cid)
        items.append(
            NarrativeSampleItem(
                case_id=cid,
                case_type=case.case_type if case else "unknown",
                final_decision=case.final_decision if case else None,
                approved_amount=(
                    str(case.approved_amount)
                    if case and case.approved_amount is not None
                    else None
                ),
                narrative=doc.body if doc else None,
                sampled_at=latest_sample[cid].created_at,
            )
        )
    return NarrativeSampleListResponse(total=len(pending_ids), items=items, stats=stats)


@router.post(
    "/cases/{case_id}/narrative-review", response_model=NarrativeReviewResponse
)
async def review_narrative(
    case_id: str,
    body: NarrativeReviewRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    staff: str | None = Depends(require_staff),
) -> NarrativeReviewResponse:
    """坐席叙述抽评：pass/revise + 评语 → narrative_review 事件落审计。

    operator（T147）：staff_keys 已配置时由 Key 派生，未配置回退 body.agent。
    """
    case = (
        await session.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="案件不存在")

    kinds = set(
        (
            await session.execute(
                select(CaseEvent.kind).where(
                    CaseEvent.case_id == case_id,
                    CaseEvent.kind.in_([NARRATIVE_SAMPLE_KIND, NARRATIVE_REVIEW_KIND]),
                )
            )
        ).scalars()
    )
    if NARRATIVE_SAMPLE_KIND not in kinds:
        raise HTTPException(status_code=404, detail="该案件不在叙述抽评样本中")
    if NARRATIVE_REVIEW_KIND in kinds:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该案件叙述已评审")

    await get_default_recorder().event(
        case_id,
        NARRATIVE_REVIEW_KIND,
        payload={
            "verdict": body.verdict,
            "comment": body.comment,
            "operator": staff or body.agent,
        },
    )
    return NarrativeReviewResponse(case_id=case_id, verdict=body.verdict)
