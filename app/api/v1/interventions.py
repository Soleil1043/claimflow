"""HITL 人工介入工单路由（T036，F10 转人工事件的坐席处理后端）。

- GET  /api/v1/interventions                    工单列表（status 筛选 + 分页，倒序）
- GET  /api/v1/interventions/{ticket_id}        工单详情 + 聚合上下文（会话轨迹/工具轨迹/
                                               Agent 步骤/合规快照/拦截原因）
- POST /api/v1/interventions/{ticket_id}/resolve    坐席解决并回写结论（pending → resolved）
- POST /api/v1/interventions/{ticket_id}/escalate  升级转出（pending → transferred_out）

状态机：pending → resolved | transferred_out，两者均为终态，仅 pending 可流转（非法流转 409）。
落单：A06 转人工（REJECT）出口调 ensure_human_ticket，一会话最多一张 open 工单（幂等）。
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_app_graph, get_db_session
from app.api.v1.conversations import to_message_item
from app.core.logging import get_logger
from schemas.api import (
    ConversationRef,
    HumanTicketDetailResponse,
    HumanTicketListResponse,
    HumanTicketSummary,
    MessageItem,
    TicketEscalateRequest,
    TicketResolveRequest,
    TicketResolveResponse,
)
from services.db.models import Conversation, HumanTicket, Message

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/interventions", tags=["interventions"])

# 终态集合（状态机流转校验用）
TERMINAL_STATUSES = {"resolved", "transferred_out"}


async def ensure_human_ticket(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: str,
    intervention_reason: str | None,
    compliance_snapshot: dict | None,
) -> HumanTicket | None:
    """转人工事件落工单（幂等：该会话存在 pending 工单时不重复落）。

    由 A06 出口（need_human=True）调用；返回新建工单，已存在 open 工单时返回 None。
    """
    existing = (
        await session.execute(
            select(HumanTicket).where(
                HumanTicket.conversation_id == conversation_id,
                HumanTicket.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.info("ticket_already_open", ticket_id=existing.id, conversation_id=str(conversation_id))
        return None

    ticket = HumanTicket(
        conversation_id=conversation_id,
        user_id=user_id,
        intervention_reason=intervention_reason,
        compliance_snapshot=compliance_snapshot,
        status="pending",
    )
    session.add(ticket)
    await session.flush()
    log.info(
        "ticket_created",
        ticket_id=ticket.id,
        conversation_id=str(conversation_id),
        user_id=user_id,
        reason=(intervention_reason or "")[:100],
    )
    return ticket


def _to_ticket_summary(t: HumanTicket) -> HumanTicketSummary:
    """ORM → 工单列表项。"""
    return HumanTicketSummary(
        id=t.id,
        conversation_id=t.conversation_id,
        user_id=t.user_id,
        status=t.status,  # type: ignore[arg-type]
        intervention_reason=t.intervention_reason,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


async def _get_ticket_or_404(ticket_id: int, session: AsyncSession) -> HumanTicket:
    ticket = (
        await session.execute(select(HumanTicket).where(HumanTicket.id == ticket_id))
    ).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket


def _ensure_pending(ticket: HumanTicket) -> None:
    """状态机守卫：仅 pending 可流转，终态再操作 409。"""
    if ticket.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"工单已处于终态 {ticket.status}，不可再流转（仅 pending 可操作）",
        )


@router.get("", response_model=HumanTicketListResponse)
async def list_tickets(
    ticket_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HumanTicketListResponse:
    """工单列表：按创建时间倒序，支持 status 筛选（坐席队列默认拉 pending）。"""
    conditions = []
    if ticket_status is not None:
        conditions.append(HumanTicket.status == ticket_status)
    total = (
        await session.execute(select(func.count(HumanTicket.id)).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(HumanTicket)
                .where(*conditions)
                .order_by(HumanTicket.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return HumanTicketListResponse(total=total, items=[_to_ticket_summary(t) for t in rows])


@router.get("/{ticket_id}", response_model=HumanTicketDetailResponse)
async def get_ticket(
    ticket_id: int,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HumanTicketDetailResponse:
    """工单详情 + 聚合上下文：会话完整轨迹（含 tool_trace/agent_steps/compliance_status
    审计字段）+ 转人工时刻的合规裁决快照 + 拦截原因。"""
    ticket = await _get_ticket_or_404(ticket_id, session)

    conversation = (
        await session.execute(select(Conversation).where(Conversation.id == ticket.conversation_id))
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="关联会话不存在")

    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == ticket.conversation_id)
                .order_by(Message.id.asc())
            )
        )
        .scalars()
        .all()
    )
    items: list[MessageItem] = [to_message_item(m) for m in messages]
    summary = _to_ticket_summary(ticket)
    return HumanTicketDetailResponse(
        **summary.model_dump(),
        compliance_snapshot=ticket.compliance_snapshot,
        resolution_note=ticket.resolution_note,
        resolved_by=ticket.resolved_by,
        conversation=ConversationRef(
            id=conversation.id,
            user_id=conversation.user_id,
            status=conversation.status,
            created_at=conversation.created_at,
        ),
        messages=items,
    )


@router.post("/{ticket_id}/resolve", response_model=TicketResolveResponse)
async def resolve_ticket(
    ticket_id: int,
    body: TicketResolveRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    graph=Depends(get_app_graph),  # noqa: B008
) -> TicketResolveResponse:
    """坐席解决工单：回写结论 + interrupt 恢复会话（T037）。

    工单 pending → resolved；若该会话的图处于 interrupt 挂起态，以
    Command(resume=坐席结论) 恢复——结论经图内 human_review 合规复审后
    作为最终回答返回；会话状态 transferred → active，坐席回复落审计。
    图无挂起（如 dev 内存 checkpoint 已随重启丢失）时不阻断：结论仍落
    工单与审计，answer 回退为坐席结论文本。
    """
    ticket = await _get_ticket_or_404(ticket_id, session)
    _ensure_pending(ticket)
    ticket.status = "resolved"
    ticket.resolution_note = body.resolution_note
    ticket.resolved_by = body.resolved_by
    # Python 时间而非 SQL 表达式：flush 后回读不触发 lazy refresh（响应要带 updated_at）
    ticket.updated_at = dt.datetime.now()
    await session.flush()
    log.info("ticket_resolved", ticket_id=ticket.id, resolved_by=body.resolved_by)

    # T037：interrupt 恢复——先检查挂起态，避免向无挂起的 thread 发 resume
    answer = body.resolution_note
    resumed = False
    review_verdict: str | None = None
    try:
        from langgraph.types import Command

        config = {"configurable": {"thread_id": str(ticket.conversation_id)}}
        snapshot = await graph.aget_state(config)
        if snapshot is not None and snapshot.next:
            result = await graph.ainvoke(
                Command(
                    resume={
                        "resolution_note": body.resolution_note,
                        "resolved_by": body.resolved_by,
                    }
                ),
                config=config,
            )
            answer = str(result.get("final_answer") or answer)
            review_verdict = (result.get("compliance_result") or {}).get("verdict")
            resumed = True
            log.info("ticket_conversation_resumed", ticket_id=ticket.id, verdict=review_verdict)
        else:
            log.info("ticket_no_pending_interrupt", ticket_id=ticket.id)
    except Exception as exc:  # noqa: BLE001 恢复失败不阻断工单闭环
        log.warning("ticket_resume_failed", ticket_id=ticket.id, error=str(exc)[:200])

    # 会话回 active（坐席已处理，用户可继续对话）+ 坐席回复落审计
    conversation = (
        await session.execute(select(Conversation).where(Conversation.id == ticket.conversation_id))
    ).scalar_one_or_none()
    if conversation is not None:
        if conversation.status == "transferred":
            conversation.status = "active"
        conversation.updated_at = dt.datetime.now()
    session.add(
        Message(
            conversation_id=ticket.conversation_id,
            role="assistant",
            content=answer,
            compliance_status=review_verdict,
        )
    )
    await session.flush()

    return TicketResolveResponse(
        ticket=_to_ticket_summary(ticket),
        answer=answer,
        resumed=resumed,
    )


@router.post("/{ticket_id}/escalate", response_model=HumanTicketSummary)
async def escalate_ticket(
    ticket_id: int,
    body: TicketEscalateRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HumanTicketSummary:
    """坐席升级转出：线下渠道处理（pending → transferred_out）。"""
    ticket = await _get_ticket_or_404(ticket_id, session)
    _ensure_pending(ticket)
    ticket.status = "transferred_out"
    ticket.resolution_note = body.note
    ticket.resolved_by = body.resolved_by
    ticket.updated_at = dt.datetime.now()
    await session.flush()
    log.info("ticket_escalated", ticket_id=ticket.id, resolved_by=body.resolved_by)
    return _to_ticket_summary(ticket)
