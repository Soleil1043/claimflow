"""API 请求/响应 Pydantic schema。

A01 健康检查 + A02-A05 会话管理（A06 发消息随 T012、A07 文件上传随 T020 补充）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class DependencyStatus(BaseModel):
    """单个依赖的健康状态。"""

    status: Literal["ok", "skipped", "error"]
    detail: str = ""


class HealthResponse(BaseModel):
    """GET /health 响应。"""

    status: Literal["ok", "degraded", "error"]
    profile: str
    dependencies: dict[str, DependencyStatus]


# ---------- A02 创建会话 ----------


class ConversationCreateRequest(BaseModel):
    """POST /api/v1/conversations 请求体。"""

    user_id: str = Field(default="demo-user", min_length=1, max_length=64)


class ConversationCreateResponse(BaseModel):
    """创建会话响应。"""

    conversation_id: uuid.UUID
    user_id: str
    status: str
    created_at: dt.datetime


# ---------- A03 会话列表 ----------


class ConversationSummary(BaseModel):
    """会话列表项。"""

    id: uuid.UUID
    user_id: str
    status: str
    created_at: dt.datetime
    message_count: int = 0


class ConversationListResponse(BaseModel):
    """GET /api/v1/conversations 响应。"""

    total: int
    items: list[ConversationSummary]


# ---------- A05 消息 ----------


class MessageItem(BaseModel):
    """单条消息（对外展示层，含审计字段）。"""

    id: int
    role: str
    content: str
    intent: str | None = None
    tool_trace: list[dict[str, Any]] | None = None
    agent_steps: list[dict[str, Any]] | None = None
    compliance_status: str | None = None
    created_at: dt.datetime


class MessageListResponse(BaseModel):
    """GET /api/v1/conversations/{id}/messages 响应。"""

    total: int
    items: list[MessageItem]


# ---------- A06 发消息（触发 Agent 流程） ----------


class MessageSendRequest(BaseModel):
    """POST /api/v1/conversations/{id}/messages 请求体。"""

    content: str = Field(min_length=1, max_length=4000)


class MessageSendResponse(BaseModel):
    """发消息响应：回答 + 意图 + 工具轨迹 + 合规状态 + 介入标记。"""

    answer: str
    intent: str | None = None
    used_tools: list[dict[str, Any]] = Field(default_factory=list)
    agent_steps: list[dict[str, Any]] | None = None
    compliance_status: str | None = None
    need_human_intervention: bool = False
    intervention_reason: str | None = None


# ---------- A07 图片上传（触发 OCR，F12） ----------


class OcrResultResponse(BaseModel):
    """POST /api/v1/conversations/{id}/images 响应：OCR 结构化字段 + 来源标记。"""

    patient_name: str | None = None
    diagnosis: str | None = None
    amount: float | None = None
    date: str | None = None
    # vision（真实识别） / mock_fallback（vision 失败降级）
    source: str
    filename: str


# ---------- A04 会话详情 ----------


class ConversationDetailResponse(BaseModel):
    """GET /api/v1/conversations/{id} 响应（会话 + 最近消息摘要）。"""

    id: uuid.UUID
    user_id: str
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime | None = None
    last_messages: list[MessageItem] = Field(default_factory=list)


# ---------- HITL 人工介入工单（T036） ----------


class HumanTicketSummary(BaseModel):
    """工单列表项（坐席队列）。"""

    id: int
    conversation_id: uuid.UUID
    user_id: str
    status: Literal["pending", "resolved", "transferred_out"]
    intervention_reason: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime | None = None


class HumanTicketListResponse(BaseModel):
    """GET /api/v1/interventions 响应（status 筛选 + 分页）。"""

    total: int
    items: list[HumanTicketSummary]


class ConversationRef(BaseModel):
    """工单详情内嵌的会话基本信息。"""

    id: uuid.UUID
    user_id: str
    status: str
    created_at: dt.datetime


class HumanTicketDetailResponse(HumanTicketSummary):
    """GET /api/v1/interventions/{id} 响应：工单 + 聚合上下文。

    聚合上下文 = 会话完整轨迹（messages，含 tool_trace / agent_steps / compliance_status
    审计字段）+ 转人工时刻的合规裁决快照（compliance_snapshot）+ 拦截原因。
    """

    compliance_snapshot: dict[str, Any] | None = None
    resolution_note: str | None = None
    resolved_by: str | None = None
    conversation: ConversationRef
    messages: list[MessageItem] = Field(default_factory=list)


class TicketResolveRequest(BaseModel):
    """POST /api/v1/interventions/{id}/resolve 请求体：解决并回写结论。"""

    resolution_note: str = Field(min_length=1, max_length=4000)
    resolved_by: str = Field(min_length=1, max_length=64)


class TicketEscalateRequest(BaseModel):
    """POST /api/v1/interventions/{id}/escalate 请求体：升级转出。"""

    note: str | None = Field(default=None, max_length=4000)
    resolved_by: str = Field(min_length=1, max_length=64)
