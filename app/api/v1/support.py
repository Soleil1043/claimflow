"""门户在线客服路由（T134，D057）。

- POST /api/v1/support/conversations                       建会话
- GET  /api/v1/support/conversations/{id}                  会话状态（门户轮询）
- GET  /api/v1/support/conversations/{id}/messages         消息历史
- POST /api/v1/support/conversations/{id}/messages         客户发消息（ai 态 AI 应答；
                                                            escalated 态只落消息，坐席应答）

v1 请求-响应式（SSE 挂 D057-3 后续）；坐席侧工单端点见 T135。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.exceptions import SupportStateError
from app.core.logging import get_logger
from schemas.api import (
    SupportConversationCreateResponse,
    SupportConversationStatusResponse,
    SupportMessageListResponse,
    SupportMessageOut,
    SupportSendMessageRequest,
    SupportSendMessageResponse,
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
