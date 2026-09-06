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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.case import ISSUED_CASE_STATUSES, CaseStatus
from services.db.models import Case, CaseJob, DecisionDocument
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
