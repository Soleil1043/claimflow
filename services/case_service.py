"""案件领域服务（T098，评审候选 6）：路由层的领域操作收口。

- 自然键幂等查询（F01）/ 案件号生成 / 建档（B01）
- Command(resume) 载荷唯一构造器——B03 补件自动恢复与工单处理（T086）两个入口
  同一 payload 形状，human_gate 消费侧只需认一种格式
- 决定书 ORM → 响应字典映射（cases 详情与 interventions 处理结果共用）

图调用（ainvoke / interrupt 恢复）留在路由——它是 HTTP 请求生命周期内的编排粘合；
管线异步化（受理即返回、后台执行）属行为变更，为后续任务预留，本模块不预设。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.case import ISSUED_CASE_STATUSES, PENDING_CASE_STATUSES, CaseStatus
from services.db.models import Case, CaseEvent, CaseIdCounter, CaseJob, DecisionDocument
from services.db.session import get_session_factory

# 自然键幂等（F01）：重复提交返回既有案件，不重复执行核赔
NATURAL_KEY_FIELDS = ("user_id", "policy_no", "claimed_amount", "incident_date")


async def find_idempotent_case(
    session: AsyncSession,
    *,
    user_id: str,
    policy_no: str,
    claimed_amount: Decimal,
    incident_date: dt.date,
) -> Case | None:
    """按自然键 (user_id, policy_no, claimed_amount, incident_date) 查既有案件。"""
    return (
        await session.execute(
            select(Case).where(
                Case.user_id == user_id,
                Case.policy_no == policy_no,
                Case.claimed_amount == claimed_amount,
                Case.incident_date == incident_date,
            )
        )
    ).scalars().first()


async def generate_case_id(session: AsyncSession) -> str:
    """按年生成业务案件号 CASE-YYYY-NNNN（计数行原子自增，T155 跨实例安全）。

    单条 upsert-returning：INSERT 分支懒自播种（首号取存量 CASE-YYYY-* 最大
    流水 +1，旧库免迁移种子），ON CONFLICT(year) DO UPDATE 自增 +1 RETURNING。
    SQLite 与 PG 同构（substr/cast/like 均原生）。取代 T150 前的"读 count→+1→
    查 exists"读-判-写——那是进程内锁才守得住的临界区，多实例并发必撞号。
    """
    year = dt.date.today().year
    # 存量最大流水（仅 INSERT 播种分支消费；计数行已存在时不参与）
    max_existing = select(
        func.coalesce(func.max(cast(func.substr(Case.id, 11), Integer)), 0)
    ).where(Case.id.like(f"CASE-{year}-%")).scalar_subquery()

    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt: Any = pg_insert(CaseIdCounter).values(year=year, last_no=max_existing + 1)
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(CaseIdCounter).values(year=year, last_no=max_existing + 1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CaseIdCounter.year],
        set_={"last_no": CaseIdCounter.last_no + 1},
    )
    no = (await session.execute(stmt.returning(CaseIdCounter.last_no))).scalar_one()
    return f"CASE-{year}-{no:04d}"


def new_case(
    *,
    case_id: str,
    user_id: str,
    policy_no: str,
    claimed_amount: Decimal,
    incident_date: dt.date,
    incident_description: str,
    materials: list[dict[str, Any]],
) -> Case:
    """建档（受理态；险种由 intake 分类写入）。"""
    return Case(
        id=case_id,
        user_id=user_id,
        policy_no=policy_no,
        case_type="unknown",
        status=CaseStatus.RECEIVED,
        claimed_amount=claimed_amount,
        incident_date=incident_date,
        incident_description=incident_description,
        materials=materials,
    )


def build_supplement_resolution(
    added_materials: list[dict[str, Any]], *, resolved_by: str
) -> dict[str, Any]:
    """补件工单的 Command(resume) 载荷（客户上传与坐席登记共用，T098 归一）。"""
    return {
        "kind": "supplement",
        "action": "upload",
        "added_materials": added_materials,
        "resolved_by": resolved_by,
    }


def build_agent_resolution(
    *,
    kind: str,
    action: str,
    decision: str | None,
    approved_amount: Decimal | None,
    reason: str | None,
    body: str | None,
    note: str | None,
    resolved_by: str,
) -> dict[str, Any]:
    """review/escape 工单的 Command(resume) 载荷（坐席处理口径）。

    kind = 案件实际挂起类型（checkpoint human_request.kind），由调用方传入。
    """
    return {
        "kind": kind,
        "action": action,
        "decision": decision,
        "approved_amount": str(approved_amount) if approved_amount else None,
        "reason": reason,
        "body": body,
        "note": note,
        "resolved_by": resolved_by,
    }


async def decision_doc_view(
    session: AsyncSession, case: Case
) -> tuple[DecisionDocument | None, bool]:
    """决定书读模型（D046 单源）：返回 (最新版, 是否已签发)。

    签发物 = 案件终态（auto_issued/closed）时的最新版——是否签发由案件状态
    推导（D006：事实态以 cases 表为准，不另立标记）。
    未签发时 doc 仍返回（草稿，供坐席复核视图），issued=False；客户视图
    由前端按 issued 决定渲染。
    """
    doc = (
        await session.execute(
            select(DecisionDocument)
            .where(DecisionDocument.case_id == case.id)
            .order_by(DecisionDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    issued = case.status in ISSUED_CASE_STATUSES
    return doc, issued


def decision_doc_payload(doc: DecisionDocument | dict[str, Any] | None) -> dict[str, Any] | None:
    """决定书统一响应字典（version/title/conclusion/approved_amount/issued_by/body）。

    接受 ORM 行（cases 查询路径）或 state dump 字典（interventions resume 返回路径）。
    """
    if doc is None:
        return None
    if isinstance(doc, DecisionDocument):
        return {
            "version": doc.version,
            "title": doc.title,
            "conclusion": doc.conclusion,
            "approved_amount": doc.approved_amount,
            "issued_by": doc.issued_by,
            "body": doc.body,
        }
    return {
        "version": doc.get("version", 1),
        "title": doc.get("title", ""),
        "conclusion": doc.get("conclusion", ""),
        "approved_amount": doc.get("approved_amount"),
        "issued_by": doc.get("issued_by", "agent"),
        "body": doc.get("body", ""),
    }


# ===== 挂起信息读模型（T106，D047：job 回执单源） =====


def human_info_from_job(
    job: CaseJob | None, case_status: str | None = None
) -> dict[str, Any]:
    """交付回执 → 挂起信息 dict（kind/reason/missing，D047/D051 单源）。

    无回执（或回执非挂起终态）时内建保守默认：supplement_pending→supplement，
    其余→review——"无回执怎么猜"只有这一份实现（D051 收编 zombie 分支与
    两前端兜底）。
    """
    if job is not None and job.outcome == "interrupted":
        payload = job.interrupt_payload or {}
        return {
            "kind": str(payload.get("kind") or "review"),
            "reason": payload.get("reason"),
            "missing": list(payload.get("missing", []) or []),
        }
    kind = (
        "supplement"
        if case_status == CaseStatus.SUPPLEMENT_PENDING
        else "review"
    )
    return {"kind": kind, "reason": None, "missing": []}


async def latest_jobs_for_cases(case_ids: list[str]) -> dict[str, CaseJob]:
    """批量取每案件的最新交付任务（工单列表单 SQL，消 N+1）。"""
    if not case_ids:
        return {}
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(CaseJob)
                .where(CaseJob.case_id.in_(case_ids))
                .order_by(CaseJob.case_id, CaseJob.id.desc())
            )
        ).scalars().all()
    latest: dict[str, CaseJob] = {}
    for row in rows:
        latest.setdefault(row.case_id, row)  # id 降序 → 首见即最新
    return latest


# ===== 客服案件进度读模型（T133，D057：与 B02 get_case 同口径的 LLM 紧凑投影） =====


async def case_progress_snapshot(case_id: str) -> dict[str, Any] | None:
    """案件进度快照：客服 case_status_query 工具的查询口径。

    与 app.api.v1.cases.get_case 共用读模型（decision_doc_view /
    human_info_from_job / latest_job），投影更紧凑——面向 LLM 作答，不含完整
    timeline / 材料清单 / 申请人记忆。自开会话（工具无请求级 session 可用）。
    """
    from services.case_jobs import latest_job

    factory = get_session_factory()
    async with factory() as session:
        case = await session.get(Case, case_id)
        if case is None:
            return None
        job = await latest_job(case_id)
        doc, issued = await decision_doc_view(session, case)
        recent_rows = (
            (
                await session.execute(
                    select(CaseEvent.kind, CaseEvent.stage, CaseEvent.created_at)
                    .where(CaseEvent.case_id == case_id)
                    .order_by(CaseEvent.seq.desc())
                    .limit(8)
                )
            ).all()
        )
    recent_rows.reverse()  # 取最近 8 条后恢复时序
    return {
        "case_id": case.id,
        "case_type": case.case_type,
        "status": case.status,
        "claimed_amount": str(case.claimed_amount),
        "approved_amount": (
            str(case.approved_amount) if case.approved_amount is not None else None
        ),
        "final_decision": case.final_decision,
        "decision_issued": issued,
        "decision_conclusion": doc.conclusion if doc is not None else None,
        "decision_approved_amount": (
            str(doc.approved_amount) if doc is not None and doc.approved_amount is not None else None
        ),
        "human": (
            human_info_from_job(job, case.status)
            if case.status in PENDING_CASE_STATUSES
            else None
        ),
        "recent_events": [
            {
                "kind": kind,
                "stage": stage,
                "at": created_at.isoformat() if created_at is not None else None,
            }
            for (kind, stage, created_at) in recent_rows
        ],
        "submitted_at": case.created_at.isoformat() if case.created_at is not None else None,
    }
