"""API 请求/响应 Pydantic schema。

A01 健康检查 + A02-A05 会话管理（A06 发消息随 T012、A07 文件上传随 T020 补充）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
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
    """POST /api/v1/conversations/{id}/materials（兼容别名 /images）响应：结构化字段 + 来源标记。"""

    patient_name: str | None = None
    diagnosis: str | None = None
    amount: float | None = None
    date: str | None = None
    # vision（图片/扫描件识别） / text_model（PDF/Word 文本提取） / mock_fallback（失败降级）
    source: str
    filename: str
    # image / pdf / docx（T049）
    file_type: str = "image"


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


class TicketResolveResponse(BaseModel):
    """resolve 响应：工单状态 + 恢复后返回用户的回答（T037 interrupt 恢复）。"""

    ticket: HumanTicketSummary
    # 坐席结论经图内合规复审后的最终回答；图无挂起（无法恢复）时为坐席结论文本
    answer: str
    # 是否实际执行了 interrupt 恢复（False = 图无挂起，仅落工单与审计）
    resumed: bool = True


class TicketEscalateRequest(BaseModel):
    """POST /api/v1/interventions/{id}/escalate 请求体：升级转出。"""

    note: str | None = Field(default=None, max_length=4000)
    resolved_by: str = Field(min_length=1, max_length=64)


# ---------- B01-B03 核赔案件（Phase 8 T080） ----------


class CaseMaterialRefIn(BaseModel):
    """案件提交时的材料引用。"""

    file_name: str = Field(min_length=1, max_length=255)
    doc_type: Literal["invoice", "diagnosis", "cost_list", "medical_record"] | None = None


class CaseCreateRequest(BaseModel):
    """POST /api/v1/cases 请求体。"""

    user_id: str = Field(min_length=1, max_length=64)
    policy_no: str = Field(min_length=1, max_length=32)
    claimed_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    incident_date: dt.date
    incident_description: str = Field(min_length=1, max_length=2000)
    # 客户自报险种（可空）；以 intake 分类为准（F01）
    declared_case_type: Literal["medical", "auto", "property", "accident"] | None = None
    materials: list[CaseMaterialRefIn] = Field(default_factory=list)


class CaseDecisionDocumentOut(BaseModel):
    """理赔决定书（版本化）。"""

    version: int
    title: str
    conclusion: str
    approved_amount: Decimal | None = None
    issued_by: str = "auto"
    body: str = ""


class CaseHumanInfo(BaseModel):
    """转人工挂起信息（supplement/review/escape）。"""

    kind: str
    reason: str | None = None
    missing: list[str] = Field(default_factory=list)


class CaseSubmitResponse(BaseModel):
    """POST /api/v1/cases 响应（201 新建 / 200 幂等命中）。"""

    case_id: str
    case_type: str
    status: str
    final_decision: str | None = None
    approved_amount: Decimal | None = None
    decision_document: CaseDecisionDocumentOut | None = None
    human: CaseHumanInfo | None = None
    idempotent: bool = False


class CaseTimelineEvent(BaseModel):
    """案件审计时间线事件（case_events，seq 升序）。"""

    seq: int
    kind: str
    stage: str | None = None
    payload: dict[str, Any] | None = None
    created_at: dt.datetime


class CaseDetailResponse(BaseModel):
    """GET /api/v1/cases/{case_id} 响应：进度/结论/决定书/审计时间线/申请人历史。"""

    case_id: str
    user_id: str
    policy_no: str
    case_type: str
    status: str
    claimed_amount: Decimal
    approved_amount: Decimal | None = None
    final_decision: str | None = None
    materials: list[dict[str, Any]] = Field(default_factory=list)
    decision_document: CaseDecisionDocumentOut | None = None
    timeline: list[CaseTimelineEvent] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime | None = None
    # 申请人历史核赔档案（T100：终态记忆检索，排除本案件；记忆关闭时为空）
    applicant_memories: list[dict[str, Any]] = Field(default_factory=list)

class CaseMaterialUploadResponse(BaseModel):
    """POST /api/v1/cases/{case_id}/materials 响应。"""

    case_id: str
    filename: str
    file_type: str
    doc_type: str | None = None
    source: str
    patient_name: str | None = None
    diagnosis: str | None = None
    amount: float | None = None
    date: str | None = None
    materials_count: int
    # 补件挂起案件上传后自动恢复的流程状态（T086；非挂起案件为 None）
    case_status: str | None = None


# ---------- T086 案件人工介入（核赔工单） ----------


class CaseInterventionHuman(BaseModel):
    """挂起案件的人工介入信息。"""

    kind: str
    reason: str | None = None
    missing: list[str] = Field(default_factory=list)


class CaseInterventionItem(BaseModel):
    """待处理核赔工单（interrupt 挂起的案件）。"""

    case_id: str
    case_type: str
    status: str
    claimed_amount: Decimal
    human: CaseInterventionHuman
    created_at: dt.datetime


class CaseInterventionListResponse(BaseModel):
    """GET /api/v1/interventions/cases 响应。"""

    total: int
    items: list[CaseInterventionItem]


class CaseResolveRequest(BaseModel):
    """POST /api/v1/interventions/cases/{case_id}/resolve 请求体。

    - supplement 工单：added_materials 传补传材料（或经 B03 上传自动恢复）
    - review 工单：action=confirm（签发既有结论）/ rewrite（改判，带 decision/
      approved_amount/body）；note/resolved_by 必填，坐席文本过红线复审
    - escape 工单：仅 note/resolved_by（转专家线下）
    """

    action: Literal["confirm", "rewrite"] | None = None
    decision: Literal["approved", "rejected", "partial"] | None = None
    approved_amount: Decimal | None = None
    reason: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=4000)
    note: str = Field(default="", max_length=1000)
    resolved_by: str = Field(default="agent", max_length=64)
    added_materials: list[CaseMaterialRefIn] = Field(default_factory=list)


class CaseResolveResponse(BaseModel):
    """工单处理响应：恢复后的案件终态。"""

    case_id: str
    status: str
    final_decision: str | None = None
    approved_amount: Decimal | None = None
    decision_document: CaseDecisionDocumentOut | None = None
