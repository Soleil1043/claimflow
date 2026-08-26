"""会话管理路由（A02-A07）。

- A02 POST /api/v1/conversations          创建会话
- A03 GET  /api/v1/conversations          会话列表（分页）
- A04 GET  /api/v1/conversations/{id}     会话详情 + 最近消息摘要
- A05 GET  /api/v1/conversations/{id}/messages  消息历史（分页，时间正序）
- A06 POST /api/v1/conversations/{id}/messages  发消息（触发 LangGraph ReAct 流程）
- A07 POST /api/v1/conversations/{id}/images    上传图片材料（vision OCR + Mock 兜底，F12）
"""

from __future__ import annotations

import base64
import time
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from langchain_core.messages import HumanMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_app_graph, get_db_session
from app.core.config import settings
from app.core.logging import get_logger
from schemas.api import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationSummary,
    MessageItem,
    MessageListResponse,
    MessageSendRequest,
    MessageSendResponse,
    OcrResultResponse,
)
from services.db.models import Conversation, Message
from services.observability import metrics

log = get_logger(__name__)

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
        await session.execute(
            select(Conversation, func.count(Message.id))
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .group_by(Conversation.id)
            .order_by(Conversation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
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


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageSendResponse,
)
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageSendRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    graph=Depends(get_app_graph),  # noqa: B008
) -> MessageSendResponse:
    """A06 发消息：完整主图流程（intent 分流 → 多 Agent / RAG / ReAct → 合规门禁）。

    F02 完整 / F08 / F10 / F14：返回 answer / intent / used_tools / agent_steps /
    compliance_status / need_human_intervention 完整结构；审计同步落库；
    REJECT 时会话标记 transferred（转人工）。
    """
    conversation = await _get_conversation_or_404(conversation_id, session)

    started = time.perf_counter()
    # T029：本轮 token 统计上下文（意图/规划/执行/生成/合规分环节归集）
    from services.observability.token_tracker import finish_turn_tokens, start_turn_tokens

    token_tracker = start_turn_tokens(str(conversation_id))

    # T035：长期记忆读注入——仅新会话首轮（本会话尚无用户消息）检索历史记忆，
    # 后续轮次本会话上下文已在 checkpoint 中不再注入；无历史/非首轮 memory_context
    # 为空串，generator 不附加记忆段，行为与无记忆时完全一致（零影响）。
    from services.memory.long_term import format_memory_context, search_memories

    memory_context = ""
    if settings.memory_enabled:
        prior_user_msgs = (
            await session.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conversation_id, Message.role == "user"
                )
            )
        ).scalar_one()
        if prior_user_msgs == 0:
            hits = await search_memories(body.content, conversation.user_id)
            if hits:
                memory_context = format_memory_context(hits)
                log.info(
                    "memory_context_injected",
                    conversation_id=str(conversation_id),
                    user_id=conversation.user_id,
                    hits=len(hits),
                )

    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=body.content)],
            "conversation_id": str(conversation_id),
            "memory_context": memory_context,
            # 每轮全量重置（checkpoint 只累积 messages，其余字段语义为"本轮"）
            "intent": None,
            "task_plan": [],
            "current_step": 0,
            "shared_data": {},
            "agent_steps": [],
            "tool_trace": [],
            "compliance_result": None,
            "compliance_rounds": 0,
            "final_answer": "",
            "need_human_intervention": False,
            "intervention_reason": None,
        },
        config={"configurable": {"thread_id": str(conversation_id)}},
    )

    answer = result.get("final_answer") or "抱歉，我暂时无法处理该问题，请稍后再试。"
    intent = result.get("intent")
    tool_trace = result.get("tool_trace") or []
    agent_steps = result.get("agent_steps") or []
    compliance = result.get("compliance_result") or {}
    compliance_status = compliance.get("verdict")
    need_human = bool(result.get("need_human_intervention"))
    intervention_reason = result.get("intervention_reason")

    # REJECT 转人工：会话状态标记（F10）
    if need_human:
        conversation.status = "transferred"

    # 审计落库：user + assistant 两条（REJECT 时 answer 已是安全话术，违规原文不落库）
    session.add(Message(conversation_id=conversation_id, role="user", content=body.content))
    session.add(
        Message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            intent=intent,
            tool_trace=tool_trace,
            agent_steps=agent_steps,
            compliance_status=compliance_status,
        )
    )
    conversation.updated_at = func.now()
    await session.flush()

    # 业务指标埋点（architecture.md 8.1）：意图 / 端到端耗时 / 合规三态 / 转人工
    metrics.record_turn(
        intent=intent or "unknown",
        duration_s=time.perf_counter() - started,
        compliance_verdict=compliance_status or "NONE",
        need_human=need_human,
    )

    # T034：长期记忆写路径——每 N 轮更新该会话记忆摘要；转人工（终态）强制写一次快照。
    # 旁路容错：内部吞掉全部异常，失败只记日志与指标，不影响主对话流。
    from services.memory.long_term import maybe_write_memory

    await maybe_write_memory(
        conversation_id=str(conversation_id),
        user_id=conversation.user_id,
        messages=result.get("messages") or [],
        force=need_human,
    )

    # T029：token 汇总（分环节日志 + Prometheus + 超预算告警，不阻断）
    token_usage = finish_turn_tokens(token_tracker)
    log.info(
        "a06_turn_done",
        conversation_id=str(conversation_id),
        intent=intent,
        tokens_total=token_usage.get("total", 0),
        tokens_prompt=token_usage.get("prompt", 0),
        tokens_completion=token_usage.get("completion", 0),
    )

    return MessageSendResponse(
        answer=answer,
        intent=intent,
        used_tools=tool_trace,
        agent_steps=agent_steps,
        compliance_status=compliance_status,
        need_human_intervention=need_human,
        intervention_reason=intervention_reason,
    )


# 允许的图片 MIME 类型（A07 入口校验，非图片 422）
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp"}


def _mime_from_upload(file: UploadFile) -> str:
    """取上传文件的 MIME（content_type 缺失时按扩展名推断）。"""
    mime = (file.content_type or "").lower()
    if mime in _ALLOWED_IMAGE_TYPES:
        return "image/jpeg" if mime == "image/jpg" else mime
    # content_type 缺失或非法：按扩展名兜底推断
    suffix = (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(suffix, "")


@router.post(
    "/{conversation_id}/images",
    response_model=OcrResultResponse,
)
async def upload_image(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OcrResultResponse:
    """A07 上传图片材料：vision OCR 提取结构化字段（F12）。

    - 非图片文件（MIME/扩展名均不匹配）→ 422
    - vision API 异常 → 工具内部降级返回预置 Mock 数据（source: mock_fallback），接口不报错
    - OCR 结果落审计消息（role=assistant，tool_trace 记录本次识别）
    """
    conversation = await _get_conversation_or_404(conversation_id, session)

    mime = _mime_from_upload(file)
    if mime not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="仅支持图片文件（png/jpeg/webp/bmp）",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="上传文件为空",
        )
    image_base64 = base64.b64encode(content).decode("ascii")

    # OCR：vision 模型提取，失败自动 Mock 兜底（工具内部保证不抛错）
    from tools.medical.ocr_extract import OcrExtractTool

    ocr = OcrExtractTool()
    result = await ocr.execute({"image_base64": image_base64, "mime_type": mime})
    data = result.data if result.success else {}

    # 审计落库：上传行为 + OCR 结果摘要（后续对话可查历史追溯）
    session.add(
        Message(
            conversation_id=conversation_id,
            role="user",
            content=f"【上传图片材料】{file.filename}",
        )
    )
    session.add(
        Message(
            conversation_id=conversation_id,
            role="assistant",
            content=(
                f"【材料识别结果】姓名：{data.get('patient_name') or '未识别'}；"
                f"诊断：{data.get('diagnosis') or '未识别'}；"
                f"金额：{data.get('amount') if data.get('amount') is not None else '未识别'}；"
                f"日期：{data.get('date') or '未识别'}；"
                f"来源：{data.get('source', 'unknown')}"
            ),
            tool_trace=[
                {
                    "tool": "ocr_extract",
                    "input": {"filename": file.filename, "mime_type": mime},
                    "output": data,
                }
            ],
        )
    )
    conversation.updated_at = func.now()
    await session.flush()

    return OcrResultResponse(
        patient_name=data.get("patient_name"),
        diagnosis=data.get("diagnosis"),
        amount=data.get("amount"),
        date=data.get("date"),
        source=data.get("source", "unknown"),
        filename=file.filename or "",
    )
