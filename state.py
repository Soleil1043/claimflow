"""AgentState：LangGraph 主图共享状态（architecture.md 5.1）。

Phase 1（单 Agent ReAct）先启用基础字段：
messages / conversation_id / tool_trace / final_answer；
intent / task_plan / 各 Agent 结果等字段随 T013/T015+ 扩展启用，
一次定义齐全，各节点按需读写。
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
    messages: Annotated[list[AnyMessage], add_messages]  # 消息累积（checkpoint 持久化）

    # ===== 意图与任务规划（T013/T017 启用） =====
    intent: str | None
    task_plan: list[dict[str, Any]]
    current_step: int

    # ===== 各 Agent 输出（T015+ 启用） =====
    medical_result: dict[str, Any] | None
    claim_result: dict[str, Any] | None
    compliance_result: dict[str, Any] | None

    # ===== 共享数据池（Agent 间传递，T017 启用） =====
    shared_data: dict[str, Any]

    # ===== 工具调用轨迹（A06 返回 used_tools 的数据源） =====
    tool_trace: list[dict[str, Any]]

    # ===== Agent 执行步骤档案（T017：每步 agent/描述/状态/耗时/结论摘要，F08 追溯） =====
    agent_steps: list[dict[str, Any]]

    # ===== 输出与介入（T018 合规启用介入标记） =====
    final_answer: str
    need_human_intervention: bool
    intervention_reason: str | None

    # ===== 合规审查（T018 启用） =====
    compliance_result: dict[str, Any] | None  # ComplianceAgentOutput（verdict/violations/risk_score/reason）
    compliance_rounds: int  # 审查轮数（MODIFY 修订闭环上限防死循环）
