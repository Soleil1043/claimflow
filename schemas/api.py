"""API 请求/响应 Pydantic schema。

A01 健康检查 + A02-A05 会话管理（A06 发消息随 T012、A07 文件上传随 T020 补充）。
"""

from __future__ import annotations

import datetime as dt
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


# ---------- B01-B03 核赔案件（Phase 8 T080） ----------


class MaterialCatalogLine(BaseModel):
    """材料目录单险种分组（T126：pack 单源，前端下拉动态化）。"""

    line: str
    label: str
    docs: list[dict[str, str]]


class MaterialCatalogResponse(BaseModel):
    """GET /api/v1/cases/material-catalog 响应。"""

    lines: list[MaterialCatalogLine]


class CaseMaterialRefIn(BaseModel):
    """案件提交时的材料引用。"""

    file_name: str = Field(min_length=1, max_length=255)
    # 合法取值白名单由险种 pack 派生（all_doc_types），创建端点统一校验（T120）
    doc_type: str | None = Field(default=None, max_length=32)


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


class CaseJobOut(BaseModel):
    """案件交付任务投影（T103：POST/详情/工单响应的 job 字段，轮询进度口径）。"""

    job_id: int
    action: str  # run | resume
    status: str  # queued | running | succeeded | dead
    outcome: str | None = None  # completed | interrupted（仅 succeeded）
    attempt: int
    max_attempts: int
    error: str | None = None


class CaseSubmitResponse(BaseModel):
    """POST /api/v1/cases 响应（201 新建 / 200 幂等命中）。

    201 为受理快照：background 档 status=received、结论字段为空，终态经
    GET /cases/{id} 轮询（job 字段跟踪交付进度）；inline 档（测试/兼容）
    dispatch 同步执行，快照即终态。幂等命中（200）恒为既有终态。
    """

    case_id: str
    case_type: str
    status: str
    final_decision: str | None = None
    approved_amount: Decimal | None = None
    decision_document: CaseDecisionDocumentOut | None = None
    human: CaseHumanInfo | None = None
    idempotent: bool = False
    job: CaseJobOut | None = None
    # 决定书是否已签发（D046：由案件状态推导；False 时 decision_document 为草稿）
    decision_issued: bool = False


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
    # 交付任务与挂起信息（T103：轮询进度 / 补件与转人工的回执，免读 checkpoint）
    job: CaseJobOut | None = None
    human: CaseHumanInfo | None = None
    decision_issued: bool = False


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
    # 补件挂起案件上传后自动恢复的流程状态（T086；非挂起案件为 None）。
    # background 档为恢复发起时的挂起态快照，终态经详情轮询
    case_status: str | None = None
    job: CaseJobOut | None = None


# ---------- T086 案件人工介入（核赔工单） ----------


class CaseInterventionItem(BaseModel):
    """待处理核赔工单（interrupt 挂起的案件）。"""

    case_id: str
    case_type: str
    status: str
    claimed_amount: Decimal
    human: CaseHumanInfo
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
    """工单处理响应（T103：受理快照 + 交付任务；终态经详情轮询，inline 档即终态）。"""

    case_id: str
    status: str
    final_decision: str | None = None
    approved_amount: Decimal | None = None
    decision_document: CaseDecisionDocumentOut | None = None
    job: CaseJobOut | None = None
    decision_issued: bool = False


# ---------- 门户在线客服（T134，D057） ----------


class SupportConversationCreateResponse(BaseModel):
    """建会话回执。"""

    conversation_id: str
    status: str
    created_at: dt.datetime


class SupportConversationStatusResponse(BaseModel):
    """会话状态（门户轮询 / 坐席视角共用）。"""

    conversation_id: str
    status: Literal["ai", "escalated", "closed"]
    escalated_reason: str | None = None
    created_at: dt.datetime
    escalated_at: dt.datetime | None = None
    closed_at: dt.datetime | None = None


class SupportMessageOut(BaseModel):
    """一条会话消息。"""

    id: int
    role: Literal["user", "assistant", "agent"]
    content: str
    created_at: dt.datetime


class SupportMessageListResponse(BaseModel):
    """会话消息历史。"""

    total: int
    items: list[SupportMessageOut]


class SupportSendMessageRequest(BaseModel):
    """客户发送消息。"""

    content: str = Field(min_length=1, max_length=4000)


class SupportSendMessageResponse(BaseModel):
    """发消息回执：ai 态携带 AI 回复；escalated 态 reply=None（坐席应答经轮询获取）。"""

    status: Literal["ai", "escalated"]
    reply: str | None = None
