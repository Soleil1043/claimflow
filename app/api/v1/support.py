"""门户在线客服路由（T134，D057）。

- POST /api/v1/support/conversations                       建会话
- GET  /api/v1/support/conversations/{id}                  会话状态（门户轮询）
- GET  /api/v1/support/conversations/{id}/messages         消息历史
- POST /api/v1/support/conversations/{id}/messages         客户发消息（ai 态 AI 应答；
                                                            escalated 态只落消息，坐席应答）

v1 请求-响应式（SSE 挂 D057-3 后续）；坐席侧工单端点见 T135。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.exceptions import SupportStateError
from app.core.logging import get_logger
from schemas.api import (
    SupportConversationCreateResponse,
    SupportConversationStatusResponse,
    SupportMessageListResponse,
    SupportMessageOut,
    SupportSendMessageRequest,
    SupportSendMessageResponse,
    SupportTicketCloseRequest,
    SupportTicketCloseResponse,
    SupportTicketDetailResponse,
    SupportTicketItem,
    SupportTicketListResponse,
    SupportTicketReplyRequest,
    SupportTicketReplyResponse,
)
from services.support import store
from services.support.agent import reply as agent_reply

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/support", tags=["support"])


async def _get_conversation_or_404(conversation_id: str):
    conv = await store.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.post(
    "/conversations",
    response_model=SupportConversationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation() -> SupportConversationCreateResponse:
    """建客服会话（状态 ai）。"""
    conv = await store.create_conversation()
    return SupportConversationCreateResponse(
        conversation_id=conv.id, status=conv.status, created_at=conv.created_at
    )


@router.get(
    "/conversations/{conversation_id}", response_model=SupportConversationStatusResponse
)
async def get_conversation_status(conversation_id: str) -> SupportConversationStatusResponse:
    """会话状态：门户 escalated 态轮询入口（新坐席消息经 /messages 拉取）。"""
    conv = await _get_conversation_or_404(conversation_id)
    return SupportConversationStatusResponse(
        conversation_id=conv.id,
        status=conv.status,
        escalated_reason=conv.escalated_reason,
        created_at=conv.created_at,
        escalated_at=conv.escalated_at,
        closed_at=conv.closed_at,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=SupportMessageListResponse,
)
async def list_conversation_messages(conversation_id: str) -> SupportMessageListResponse:
    """消息历史：客户 / AI / 坐席三方时间线（门户与坐席共用口径）。"""
    await _get_conversation_or_404(conversation_id)
    msgs = await store.list_messages(conversation_id)
    return SupportMessageListResponse(
        total=len(msgs),
        items=[
            SupportMessageOut(
                id=m.id, role=m.role, content=m.content, created_at=m.created_at
            )
            for m in msgs
        ],
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SupportSendMessageResponse,
)
async def send_conversation_message(
    conversation_id: str, body: SupportSendMessageRequest
) -> SupportSendMessageResponse:
    """客户发消息。

    - ai：AI 应答（reply 随响应返回；转人工发生时返回终态 escalated + 转接话术）
    - escalated：AI 停答——消息落库供坐席处理，reply=None（前端展示转人工中并轮询）
    - closed：409（终态会话不可续）
    - LLM 故障：用户消息已落库，503 引导重试（下一条消息重试，不丢时间线）
    """
    conv = await _get_conversation_or_404(conversation_id)
    if conv.status == store.CONV_AI:
        try:
            text = await agent_reply(conversation_id, body.content)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="会话不存在") from exc
        except SupportStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="会话状态已变更，请刷新后重试",
            ) from exc
        except Exception as exc:  # noqa: BLE001 —— LLM/工具故障：消息已落库，引导重试
            log.warning(
                "support_reply_failed",
                conversation_id=conversation_id,
                error=str(exc)[:200],
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="在线客服暂时不可用，请稍后重试",
            ) from exc
        # 本轮可能已触发转人工（reply 内确定性流转）——回读终态供前端切换轮询
        final_status = (await store.get_conversation(conversation_id)).status
        return SupportSendMessageResponse(status=final_status, reply=text)
    if conv.status == store.CONV_ESCALATED:
        await store.append_message(
            conversation_id, role=store.ROLE_USER, content=body.content
        )
        return SupportSendMessageResponse(status=store.CONV_ESCALATED, reply=None)
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="会话已结束，不可继续发送")


# ---------- 坐席侧客服工单（T135，D057-3：三件套后端） ----------


def _message_out(m) -> SupportMessageOut:  # noqa: ANN001 —— SupportMessage ORM 行
    return SupportMessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)


@router.get("/tickets", response_model=SupportTicketListResponse)
async def list_support_tickets(
    limit: int = Query(default=50, ge=1, le=200),
) -> SupportTicketListResponse:
    """坐席工单列表：escalated 会话队列（转人工时间倒序）+ 最新消息预览。"""
    convs = await store.list_escalated_conversations(limit=limit)
    last_msgs = await store.last_messages_by_conversation([c.id for c in convs])
    items = [
        SupportTicketItem(
            conversation_id=c.id,
            escalated_reason=c.escalated_reason,
            created_at=c.created_at,
            escalated_at=c.escalated_at or c.created_at,
            last_message=(
                _message_out(last_msgs[c.id]) if c.id in last_msgs else None
            ),
        )
        for c in convs
    ]
    return SupportTicketListResponse(total=len(items), items=items)


@router.get(
    "/tickets/{conversation_id}", response_model=SupportTicketDetailResponse
)
async def get_support_ticket(conversation_id: str) -> SupportTicketDetailResponse:
    """坐席工单详情：会话状态 + 完整 transcript（closed 会话可查作审计）。"""
    conv = await _get_conversation_or_404(conversation_id)
    msgs = await store.list_messages(conversation_id)
    return SupportTicketDetailResponse(
        conversation_id=conv.id,
        status=conv.status,
        escalated_reason=conv.escalated_reason,
        created_at=conv.created_at,
        escalated_at=conv.escalated_at,
        closed_at=conv.closed_at,
        messages=[_message_out(m) for m in msgs],
    )


@router.post(
    "/tickets/{conversation_id}/reply", response_model=SupportTicketReplyResponse
)
async def reply_support_ticket(
    conversation_id: str, body: SupportTicketReplyRequest
) -> SupportTicketReplyResponse:
    """坐席回复（role=agent 入 transcript，门户 escalated 态轮询可见）。"""
    conv = await _get_conversation_or_404(conversation_id)
    if conv.status != store.CONV_ESCALATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"会话状态为 {conv.status}，仅转人工中的会话可回复",
        )
    msg = await store.append_message(
        conversation_id, role=store.ROLE_AGENT, content=body.content
    )
    return SupportTicketReplyResponse(status=store.CONV_ESCALATED, message=_message_out(msg))


@router.post(
    "/tickets/{conversation_id}/close", response_model=SupportTicketCloseResponse
)
async def close_support_ticket(
    conversation_id: str, body: SupportTicketCloseRequest
) -> SupportTicketCloseResponse:
    """关闭工单：note 先作为最后一条坐席消息入 transcript，再流转终态（此后全停）。"""
    conv = await _get_conversation_or_404(conversation_id)
    if conv.status != store.CONV_ESCALATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"会话状态为 {conv.status}，仅转人工中的会话可关闭",
        )
    try:
        if body.note:
            await store.append_message(
                conversation_id, role=store.ROLE_AGENT, content=body.note
            )
        closed = await store.close_conversation(conversation_id)
    except SupportStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="会话状态已变更，请刷新"
        ) from exc
    return SupportTicketCloseResponse(
        conversation_id=closed.id,
        status=store.CONV_CLOSED,
        closed_at=closed.closed_at,
    )
