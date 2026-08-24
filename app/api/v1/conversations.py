"""会话管理路由（A02-A05，F02/F14）。

- A02 POST /api/v1/conversations          创建会话
- A03 GET  /api/v1/conversations          会话列表（分页）
- A04 GET  /api/v1/conversations/{id}     会话详情 + 最近消息摘要
- A05 GET  /api/v1/conversations/{id}/messages  消息历史（分页，时间正序）

A06（发消息，触发 LangGraph 流程）随 T012 实现。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from schemas.api import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationSummary,
    MessageItem,
    MessageListResponse,
)
from services.db.models import Conversation, Message

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post("", response_model=ConversationCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreateRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationCreateResponse:
    """A02 创建会话：id 即 LangGraph thread_id（checkpoint 持久化键）。"""
    conversation = Conversation(user_id=body.user_id)
    session.add(conversation)
    await session.flush()  # 拿到主键与 server default
    return ConversationCreateResponse(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        status=conversation.status,
        created_at=conversation.created_at,
    )


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationListResponse:
    """A03 会话列表：按创建时间倒序 + 消息计数。"""
    total = (await session.execute(select(func.count(Conversation.id)))).scalar_one()

    rows = (
        (
            await session.execute(
                select(Conversation, func.count(Message.id))
                .outerjoin(Message, Message.conversation_id == Conversation.id)
                .group_by(Conversation.id)
                .order_by(Conversation.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .all()
    )
    items = [
        ConversationSummary(
            id=conv.id,
            user_id=conv.user_id,
            status=conv.status,
            created_at=conv.created_at,
            message_count=count,
        )
        for conv, count in rows
    ]
    return ConversationListResponse(total=total, items=items)


async def _get_conversation_or_404(
    conversation_id: uuid.UUID, session: AsyncSession
) -> Conversation:
    conversation = (
        await session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationDetailResponse:
    """A04 会话详情：附最近 5 条消息摘要。"""
    conversation = await _get_conversation_or_404(conversation_id, session)

    recent = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    return ConversationDetailResponse(
        id=conversation.id,
        user_id=conversation.user_id,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        last_messages=[_to_message_item(m) for m in reversed(recent)],
    )


@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> MessageListResponse:
    """A05 消息历史：按 id 正序（时间序）。"""
    await _get_conversation_or_404(conversation_id, session)

    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return MessageListResponse(
        total=len(messages),
        items=[_to_message_item(m) for m in messages],
    )


def _to_message_item(m: Message) -> MessageItem:
    """ORM → 对外展示模型。"""
    return MessageItem(
        id=m.id,
        role=m.role,
        content=m.content,
        intent=m.intent,
        tool_trace=m.tool_trace,
        agent_steps=m.agent_steps,
        compliance_status=m.compliance_status,
        created_at=m.created_at,
    )
