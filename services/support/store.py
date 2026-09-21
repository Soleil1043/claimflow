"""客服会话域读写与状态机（T132，D057-2/D057-3）。

单源原则：support_conversations / support_messages 是会话状态与消息的唯一权威，
兼作 UI 历史与坐席 transcript 投影；客服 Agent 多轮记忆由本表 replay（D057-2，
不接 checkpointer——转人工后坐席消息与用户消息须在同一时间线）。

状态机：ai → escalated（转人工，AI 停答）→ closed；ai → closed 合法；
closed 不可逆。非法流转抛 SupportStateError，会话不存在抛 LookupError——
均为正常业务信号，API/工具层捕获翻译，不告警。
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SupportStateError
from app.core.logging import get_logger
from services.db.models import SupportConversation, SupportMessage
from services.db.session import get_session_factory

log = get_logger(__name__)

# ===== 会话状态（D057-3） =====
CONV_AI = "ai"
CONV_ESCALATED = "escalated"
CONV_CLOSED = "closed"

# 合法流转（from → {to}）：ai 可转人工或直接关闭；escalated 只能关闭；closed 终态
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CONV_AI: frozenset({CONV_ESCALATED, CONV_CLOSED}),
    CONV_ESCALATED: frozenset({CONV_CLOSED}),
    CONV_CLOSED: frozenset(),
}

# ===== 消息角色 =====
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_AGENT = "agent"

# 各状态下允许写入的角色：escalated 后 AI 停答（坐席接管）；closed 全停
_ALLOWED_ROLES: dict[str, frozenset[str]] = {
    CONV_AI: frozenset({ROLE_USER, ROLE_ASSISTANT}),
    CONV_ESCALATED: frozenset({ROLE_USER, ROLE_AGENT}),
    CONV_CLOSED: frozenset(),
}


async def create_conversation() -> SupportConversation:
    """新建会话（状态 ai）。id 为 uuid4 hex，客户端持久持有。"""
    conv = SupportConversation(id=uuid.uuid4().hex, status=CONV_AI)
    factory = get_session_factory()
    async with factory() as session:
        session.add(conv)
        await session.commit()
        await session.refresh(conv)
    return conv


async def get_conversation(conversation_id: str) -> SupportConversation | None:
    """按 id 取会话；不存在返回 None。"""
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(SupportConversation, conversation_id)


async def append_message(
    conversation_id: str, *, role: str, content: str
) -> SupportMessage:
    """追加一条消息（append-only）。

    Raises:
        LookupError: 会话不存在。
        SupportStateError: 当前状态不允许该角色写入（escalated 后 AI 停答 /
            closed 全停）。
    """
    factory = get_session_factory()
    async with factory() as session:
        conv = await session.get(SupportConversation, conversation_id)
        if conv is None:
            raise LookupError(f"conversation not found: {conversation_id}")
        if role not in _ALLOWED_ROLES.get(conv.status, frozenset()):
            raise SupportStateError(
                f"role {role} not allowed in status {conv.status}"
            )
        msg = SupportMessage(conversation_id=conversation_id, role=role, content=content)
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
    return msg


async def list_messages(conversation_id: str) -> list[SupportMessage]:
    """按写入序（id 升序）取全部消息。"""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SupportMessage)
            .where(SupportMessage.conversation_id == conversation_id)
            .order_by(SupportMessage.id)
        )
        return list(result.scalars().all())


async def recent_messages(conversation_id: str, limit: int) -> list[SupportMessage]:
    """取最近 limit 条消息（仍按写入序升序返回）——Agent replay 窗口用。"""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SupportMessage)
            .where(SupportMessage.conversation_id == conversation_id)
            .order_by(SupportMessage.id.desc())
            .limit(limit)
        )
        msgs = list(result.scalars().all())
    msgs.reverse()
    return msgs


async def escalate_conversation(
    conversation_id: str, *, reason: str | None = None
) -> SupportConversation:
    """转人工（ai → escalated）：AI 停答，坐席接管。记录原因供工单展示。

    Raises:
        LookupError: 会话不存在。
        SupportStateError: 非法流转（已 escalated / 已 closed）。
    """
    factory = get_session_factory()
    async with factory() as session:
        conv = await _transition(session, conversation_id, CONV_ESCALATED)
        conv.escalated_at = dt.datetime.now()
        if reason:
            conv.escalated_reason = reason
        await session.commit()
    log.info(
        "support_conversation_escalated",
        conversation_id=conversation_id,
        reason=reason,
    )
    return conv


async def close_conversation(conversation_id: str) -> SupportConversation:
    """关闭会话（escalated|ai → closed）：终态，此后任何写入被拒。

    Raises:
        LookupError: 会话不存在。
        SupportStateError: 已 closed。
    """
    factory = get_session_factory()
    async with factory() as session:
        conv = await _transition(session, conversation_id, CONV_CLOSED)
        conv.closed_at = dt.datetime.now()
        await session.commit()
    log.info("support_conversation_closed", conversation_id=conversation_id)
    return conv


async def _transition(
    session: AsyncSession, conversation_id: str, to_status: str
) -> SupportConversation:
    """状态流转公共段：校验合法后在调用方 session 内改状态（不 commit）。"""
    conv = await session.get(SupportConversation, conversation_id)
    if conv is None:
        raise LookupError(f"conversation not found: {conversation_id}")
    if to_status not in _ALLOWED_TRANSITIONS.get(conv.status, frozenset()):
        raise SupportStateError(
            f"illegal transition {conv.status} -> {to_status}"
        )
    conv.status = to_status
    return conv
