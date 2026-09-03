"""LangGraph 状态定义。

v1（咨询产品，冻结于 tag v1-consultation）：AgentState——main_graph.py 仍在使用，
T093 删旧代码时随主图一并移除。
v2（核赔平台，Phase 8 T079）：ClaimCaseState——案件主图共享状态（v2 架构文档 5.1）。
"""

from __future__ import annotations

import operator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """主图状态。total=False：各节点局部更新，无需全量初始化。"""

    # ===== 对话基础 =====
    conversation_id: str                     # 对话 ID
    messages: Annotated[list[AnyMessage], add_messages]  # 消息累积（含 Worker/React 工具轨迹）

    # ===== 意图与调度 =====
    intent: str | None                       # IntentType 枚举值
    task_plan: list[dict[str, Any]]          # supervisor 计划（{agent, description, status, ...}）

    # ===== 共享数据池（Worker 结论 / RAG 上下文） =====
    shared_data: dict[str, Any]

    # ===== 输出与介入 =====
    final_answer: str                       # 最终回答
    need_human_intervention: bool           # 是否需要人工介入
    intervention_reason: str | None         # 人工介入原因

    # ===== 合规审查 =====
    compliance_result: dict[str, Any] | None  # ComplianceAgentOutput dump（verdict/violations/...）
    compliance_rounds: int                    # 审查轮数（MODIFY 修订闭环上限防死循环）

    # ===== 长期记忆（T035） =====
    # 新会话首轮按 user_id 检索的历史会话摘要（已拼装文本）；
    # 由 A06 入口写入、回答节点注入提示词，空串 = 无历史不注入
    memory_context: str


class ClaimCaseState(TypedDict, total=False):
    """核赔案件主图共享状态（T079，v2 架构文档 5.1）。

    total=False：各节点局部更新；阶段结论字段各只有一个写者（字段所有权表见架构文档 5.3，
    并行分支写不同 channel，无需合并 reducer）；errors 显式追加。
    注意：claimed_amount 用 Decimal——InMemorySaver（dev/测试）直接持对象；
    prod checkpoint 序列化兼容性在 T092 容器化时验证。
    """

    # ===== 案件标识与事实（intake/API 写入） =====
    case_id: str                             # 案件 ID
    user_id: str                             # 用户 ID
    policy_id: str                           # 保单号
    claimed_amount: Decimal                  # 索赔金额
    incident_date: date                      # 出险日期
    incident_description: str                # 出险描述
    materials: list[dict[str, Any]]          # [{file_name, doc_type?, storage_path?, note?}]
    declared_case_type: str | None           # 客户自报险种（可空）
    # intake 分类写入：medical / auto / property / accident / unknown
    case_type: str                           # 险种

    # ===== 阶段结论（各字段唯一写者；值为 schemas.stages 模型 dump） =====
    material: dict[str, Any] | None          # MaterialsAgentOutput dump
    policy: dict[str, Any] | None            # PolicyAgentOutput dump
    risk: dict[str, Any] | None              # RiskAgentOutput dump
    liability: dict[str, Any] | None         # LiabilityAgentOutput dump
    calc: dict[str, Any] | None              # CalculationAgentOutput dump
    decision: dict[str, Any] | None          # DecisionAgentOutput dump
    compliance: dict[str, Any] | None        # ComplianceAgentOutput dump

    # ===== 调度 =====
    task_plan: list[dict[str, Any]]          # orchestrator 计划快照（审计/时间线）
    routing_calls: int                       # orchestrator 已调用次数（预算控制，D039）
    # orchestrator 写入的本轮派发目标；由 route_dispatch 条件边消费（Send 并行派发）
    pending_dispatch: list[str] | None

    # ===== 交互与异常 =====
    messages: Annotated[list[AnyMessage], add_messages]
    human_request: dict[str, Any] | None     # interrupt 载荷 {kind: supplement/review/escape, ...}
    human_resolution: dict[str, Any] | None  # Command(resume) 回写
    errors: Annotated[list[dict[str, Any]], operator.add]  # [{stage, error}]

    # ===== 最终产出 =====
    final_decision: str | None               # approved / rejected / partial / referred
    decision_document: dict[str, Any] | None # DecisionDocOutput dump
