"""核赔案件主图组装（Phase 8 T079，D039）。

结构：

    START → intake ─┬─ orchestrator ⇄ {material_review, policy_verify ∥ fraud_check,
                    │                  liability_judge, amount_calc}（worker 静态回边）
                    │        orchestrator ── decision_generate（终局单派）
                    └─ human_gate（未上线险种受理转人工）
    decision_generate → compliance_gate（静态边，不可被调度绕过，D039）
    compliance_gate ─┬─ pass → auto_adjudicate ─┬─ issue → END
                     ├─ modify → revise_decision → compliance_gate
                     └─ reject → human_gate
    human_gate（interrupt 挂起）─┬─ supplement 恢复 → material_review → orchestrator
                                └─ review/escape 恢复 → END（T086 接坐席闭环）

依赖注入：recorder（案件审计）/policy_lookup/fraud_lookup 均可注入——
图测试零 DB、零 LLM；运行时 create_default_case_graph() 接 DB 实现。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from nodes.amount_calc import make_amount_calc_node
from nodes.auto_adjudicate import adjudication_route, make_auto_adjudicate_node
from nodes.compliance_gate import (
    compliance_route,
    make_compliance_gate_node,
    make_revise_decision_node,
)
from nodes.decision_generate import make_decision_generate_node
from nodes.fraud_check import make_fraud_check_node
from nodes.human_gate import human_gate_route, make_human_gate_node
from nodes.intake import make_intake_node, route_after_intake
from nodes.liability_judge import make_liability_judge_node
from nodes.material_review import make_material_review_node
from nodes.orchestrator import make_orchestrator_node, route_dispatch
from nodes.policy_verify import make_policy_verify_node
from schemas.case import CaseInputState, CaseOutput
from services.case_store import CaseRecorder, DbCaseRecorder
from state import ClaimCaseState


def build_case_graph(
    *,
    recorder: CaseRecorder,
    policy_lookup: Any,
    fraud_lookup: Any,
    checkpointer: BaseCheckpointSaver | None = None,
    orchestrator_router: Any = None,
    material_reviewer: Any = None,
    liability_invoker: Any = "__keyword__",
    decision_writer: Any = "__fallback__",
) -> Any:
    """编译核赔案件主图（依赖注入版——测试注入内存实现）。

    orchestrator_router：LLM 路由器（async state→RoutingDecision）；None = 纯确定性
    兜底编排（测试零 LLM）。material_reviewer：材料 AI 一致性审查器；None = 仅规则层。
    运行时经 create_default_case_graph 注入两者。
    """
    builder = StateGraph(ClaimCaseState, input_schema=CaseInputState, output_schema=CaseOutput)

    builder.add_node("intake", make_intake_node(recorder, policy_lookup))
    builder.add_node(
        "orchestrator", make_orchestrator_node(recorder, orchestrator_router)
    )
    builder.add_node(
        "material_review", make_material_review_node(recorder, material_reviewer)
    )
    builder.add_node("policy_verify", make_policy_verify_node(recorder, policy_lookup))
    builder.add_node("fraud_check", make_fraud_check_node(recorder, fraud_lookup))
    builder.add_node(
        "liability_judge",
        make_liability_judge_node(recorder, invoker=liability_invoker),
    )
    builder.add_node("amount_calc", make_amount_calc_node(recorder))
    builder.add_node(
        "decision_generate", make_decision_generate_node(recorder, decision_writer)
    )
    builder.add_node("compliance_gate", make_compliance_gate_node(recorder))
    builder.add_node(
        "revise_decision", make_revise_decision_node(recorder)
    )
    builder.add_node("auto_adjudicate", make_auto_adjudicate_node(recorder))
    builder.add_node("human_gate", make_human_gate_node(recorder))

    builder.add_edge(START, "intake")
    builder.add_conditional_edges(
        "intake",
        route_after_intake,
        {"orchestrator": "orchestrator", "human": "human_gate"},
    )
    # orchestrator 条件边：消费 pending_dispatch → Send 并行派发（文档化 fan-out 范式）；
    # human_request 置位 → human_gate
    builder.add_conditional_edges("orchestrator", route_dispatch)
    # 工作层：worker 完成 → 回 orchestrator（decision_generate 例外，静态进合规链）
    for worker in (
        "material_review",
        "policy_verify",
        "fraud_check",
        "liability_judge",
        "amount_calc",
    ):
        builder.add_edge(worker, "orchestrator")
    builder.add_edge("decision_generate", "compliance_gate")
    builder.add_conditional_edges(
        "compliance_gate",
        compliance_route,
        {"pass": "auto_adjudicate", "modify": "revise_decision", "reject": "human_gate"},
    )
    builder.add_edge("revise_decision", "compliance_gate")
    builder.add_conditional_edges(
        "auto_adjudicate",
        adjudication_route,
        {"issue": END, "human": "human_gate"},
    )
    # human_gate / orchestrator 动态路由：orchestrator 走 Command(goto)（受守卫约束），
    # human_gate 消费 interrupt 后按条件边分流（见 nodes/human_gate.py 实现约定）
    builder.add_conditional_edges(
        "human_gate",
        human_gate_route,
        {"material_review": "material_review", "end": END},
    )
    return builder.compile(checkpointer=checkpointer)


async def db_policy_lookup(policy_no: str) -> dict[str, Any] | None:
    """DB 保单查询（运行时默认实现；T083 起携带条款要素供保单核验消费）。"""
    from sqlalchemy import select

    from services.db.models import Policy
    from services.db.session import get_session_factory
    from tools.claim.policy_query import get_policy_terms

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(select(Policy).where(Policy.policy_no == policy_no))
        ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "policy_no": row.policy_no,
        "holder_id_card": row.holder_id_card,
        "product_type": row.product_type,
        "status": row.status,
        "coverage_amount": str(row.coverage_amount),
        "deductible": str(row.deductible),
        "payout_ratio": str(row.payout_ratio),
        "effective_date": row.effective_date.isoformat(),
        "expiry_date": row.expiry_date.isoformat(),
        "terms": get_policy_terms(row.product_type),
    }


async def db_fraud_lookup(state: dict[str, Any]) -> dict[str, Any] | None:
    """真实风控信号（T083）：黑名单（mock JSON）+ 理赔频率（claim_records 90 天）。

    经案件 policy_id 解析持有人证件号（黑名单/频率均按证件号口径）。
    """
    from tools.fraud.blacklist import query_blacklist_by_id
    from tools.fraud.history import count_recent_claims

    policy = await db_policy_lookup(str(state.get("policy_id") or ""))
    id_card = str(policy.get("holder_id_card")) if policy else None
    if not id_card:
        return None
    blacklist = query_blacklist_by_id(id_card)
    recent = await count_recent_claims(id_card, days=90)
    return {
        "blacklisted": blacklist["blacklisted"],
        "blacklist_reason": blacklist.get("reason"),
        "recent_claims": recent,
    }


def create_default_case_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """运行时便捷工厂：DB 记录器 + DB 查询 + LLM orchestrator + 内存 checkpointer。

    orchestrator_llm_enabled=False（配置）时降级为纯确定性编排（D039 安全设计 3 的
    常驻形态）。checkpointer 缺省 InMemorySaver——interrupt 挂起/断点续跑必须有
    checkpointer，生产经 main.py 注入 AsyncPostgresSaver。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    from nodes.liability_judge import keyword_only_invoker
    from nodes.material_review import make_material_ai_reviewer
    from nodes.orchestrator import make_llm_router

    return build_case_graph(
        recorder=DbCaseRecorder(),
        policy_lookup=db_policy_lookup,
        fraud_lookup=db_fraud_lookup,
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        orchestrator_router=make_llm_router(),
        material_reviewer=make_material_ai_reviewer(),
        liability_invoker=(
            None if settings.liability_llm_enabled else keyword_only_invoker
        ),
        decision_writer=(
            None if settings.decision_writer_llm_enabled else "__fallback__"
        ),
    )
