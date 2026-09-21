"""LangGraph 状态定义：核赔案件主图共享状态（T079）。"""

from __future__ import annotations

import operator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Final

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# 图 State 的 schema 版本（T141，D061）：intake 写入 state，resume 前比对——
# 不匹配（含旧 checkpoint 无此字段）降级全新重跑（案件事实权威在 cases 表 D006，
# 重跑无损）。bump 时机：State 字段增删改 / channel 语义变化时人工 +1（D061）。
CASE_SCHEMA_VERSION: Final[int] = 1


class ClaimCaseState(TypedDict, total=False):
    """LangGraph 状态：核赔案件主图共享状态（T079）。

    total=False：各节点局部更新；阶段结论字段各只有一个写者（唯一写者见各字段注释，
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
    # 图 schema 版本（intake 写入 CASE_SCHEMA_VERSION；resume 门卫消费，T141）
    schema_version: int

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
