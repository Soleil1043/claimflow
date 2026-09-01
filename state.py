"""AgentState：LangGraph 主图共享状态（architecture.md 5.1 v2）。

T047 精简（D021/ADR-007）：调度改 supervisor 动态路由（Command(goto)），
游标与手写簿记字段移除——
- current_step：supervisor 每轮按 shared_data 已有结论 + task_plan 状态推进
- tool_trace：A06/评测从 messages 派生（agents.runner.derive_tool_trace）
- agent_steps：由 task_plan（supervisor 维护状态）+ shared_data 摘要推导
- medical_result/claim_result：自 shared_data 承载起即闲置，删除
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """主图状态。total=False：各节点局部更新，无需全量初始化。"""

    # ===== 对话基础 =====
    conversation_id: str
    messages: Annotated[list[AnyMessage], add_messages]  # 消息累积（含 Worker/React 工具轨迹）

    # ===== 意图与调度 =====
    intent: str | None                       # IntentType 枚举值（T045 结构化输出）
    task_plan: list[dict[str, Any]]          # supervisor 计划（{agent, description, status, ...}）

    # ===== 共享数据池（Worker 结论 / RAG 上下文） =====
    shared_data: dict[str, Any]

    # ===== 输出与介入 =====
    final_answer: str
    need_human_intervention: bool
    intervention_reason: str | None

    # ===== 合规审查 =====
    compliance_result: dict[str, Any] | None  # ComplianceAgentOutput dump（verdict/violations/...）
    compliance_rounds: int                    # 审查轮数（MODIFY 修订闭环上限防死循环）

    # ===== 长期记忆（T035） =====
    # 新会话首轮按 user_id 检索的历史会话摘要（已拼装文本）；
    # 由 A06 入口写入、回答节点注入提示词，空串 = 无历史不注入
    memory_context: str
