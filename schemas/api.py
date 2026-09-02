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


# ---------- T051 评测 ----------


class EvalRunStartRequest(BaseModel):
    """POST /api/v1/evals/runs 请求体（与 evals.test_suite CLI 参数对应）。"""

    dataset: str = Field(default="main", description="数据集名（main / graph_assoc）")
    category: str | None = Field(default=None, description="分类过滤（None=全部）")
    limit: int | None = Field(default=None, ge=1, description="用例数上限（None=全量）")
    variant: str = Field(default="baseline", description="实验变体（evals/variants.py 注册表）")


class EvalRunBrief(BaseModel):
    """运行记录摘要（内存运行与 DB 历史行共用）。"""

    run_id: str
    status: str
    created_at: str
    finished_at: str | None = None
    params: EvalRunStartRequest
    source: str = "ui"  # ui / cli（T052）
    git_sha: str = "unknown"
    task_completion_rate: float | None = None
    tool_accuracy: float | None = None


class EvalRunStartResponse(BaseModel):
    """POST /api/v1/evals/runs 响应。"""

    run: EvalRunBrief


class EvalRunListResponse(BaseModel):
    """GET /api/v1/evals/runs 响应。"""

    runs: list[EvalRunBrief]


class EvalRunStatusResponse(BaseModel):
    """GET /api/v1/evals/runs/{run_id} 响应：状态 + 进度 + 日志尾。"""

    run_id: str
    status: str
    created_at: str
    finished_at: str | None = None
    params: EvalRunStartRequest
    current: int = 0
    total: int = 0
    passed: int = 0
    failed: int = 0
    return_code: int | None = None
    git_sha: str = "unknown"
    source: str = "ui"
    task_completion_rate: float | None = None
    tool_accuracy: float | None = None
    report_name: str | None = None
    log_tail: list[str] = Field(default_factory=list, description="运行日志末尾（最多 200 行）")


class EvalReportBrief(BaseModel):
    """报告文件摘要（evals/reports/*.json，test_suite 报告口径）。"""

    name: str
    generated_at: str = ""
    dataset: str = ""
    variant: str = ""
    category: str | None = None
    total: int = 0
    passed: int = 0
    task_completion_rate: float = 0.0
    tool_accuracy: float = 0.0
    compliance_pass_rate: float = 0.0
    avg_duration_s: float = 0.0


class EvalReportListResponse(BaseModel):
    """GET /api/v1/evals/reports 响应（新→旧）。"""

    reports: list[EvalReportBrief]


class EvalVariantInfo(BaseModel):
    """可用实验变体。"""

    name: str
    description: str = ""


class EvalMetaResponse(BaseModel):
    """GET /api/v1/evals/meta 响应：表单下拉可选项。"""

    datasets: list[str]
    categories: list[str]
    variants: list[EvalVariantInfo]


class EvalTrendPoint(BaseModel):
    """趋势图数据点（T053）：一次完成态评测的指标快照。"""

    time: str = Field(description="时间（%Y-%m-%d %H:%M:%S，升序 x 轴）")
    dataset: str = ""
    variant: str = ""
    task_completion_rate: float = 0.0
    tool_accuracy: float = 0.0
    passed: int = 0
    total: int = 0
    git_sha: str = "unknown"
    # db（eval_runs 历史行）/ report（reports 目录文件，历史存量）
    source: str = "report"
    label: str = Field(default="", description="点标签（run_id 或报告文件名，hover 展示）")
    task_completion_ci: list[float] | None = Field(
        default=None,
        description="完成率 Wilson 95% CI（T073；旧报告无此列为 None）",
    )


class EvalTrendsResponse(BaseModel):
    """GET /api/v1/evals/trends 响应（时间升序，UI 侧按数据集/变体过滤）。"""

    points: list[EvalTrendPoint]
