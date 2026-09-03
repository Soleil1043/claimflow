"""核赔案件主图端到端测试（T079）：24 金样本（兜底编排 + 桩工具）金额/路由断言。

零 DB、零 LLM：policy/fraud 查询与案件审计均注入内存实现。
路由断言口径（与 cases.json expected 块对齐）：
- auto → 正常走完，final_decision 由责任结论推导，核定金额精确到分
- human → 图 interrupt 挂起（review/escape），带金额期望的断言理算结果
- supplement → interrupt（补件），缺失清单精确匹配；另含恢复续跑 e2e
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from workflows.case_graph import build_case_graph

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "mock"
CASES = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
POLICIES = {
    p["policy_no"]: p
    for p in json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))
}

# T083 将落到 data/mock 名单文件；T079 测试内注入（与 cases.json fraud 期望对齐）
FRAUD_DATA = {
    "u-zhaomin": {"blacklisted": True},
    "u-sunqiang": {"recent_claims": 2},
}

# 补件期望缺失清单（材料清单规则：医疗险 = 发票/诊断证明/费用清单）
EXPECTED_MISSING = {
    "CASE-2026-0023": ["费用清单"],
    "CASE-2026-0024": ["医疗发票", "诊断证明"],
}


class ListRecorder:
    """内存审计记录器（测试注入）。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append(
            {"case_id": case_id, "kind": kind, "stage": stage, "payload": payload}
        )

    async def update_case(self, case_id, **kwargs) -> None:
        self.updates.append({"case_id": case_id, **kwargs})


async def _policy_lookup(policy_no: str) -> dict[str, Any] | None:
    return POLICIES.get(policy_no)


async def _fraud_lookup(user_id: str) -> dict[str, Any] | None:
    return FRAUD_DATA.get(user_id)


def _graph() -> tuple[Any, ListRecorder]:
    recorder = ListRecorder()
    graph = build_case_graph(
        recorder=recorder,
        policy_lookup=_policy_lookup,
        fraud_lookup=_fraud_lookup,
        checkpointer=InMemorySaver(),
    )
    return graph, recorder


def _input(case: dict[str, Any]) -> dict[str, Any]:
    """cases.json 用 policy_no（与 DB 列同名）；图输入 schema 为 policy_id，此处映射。"""
    stripped = {k: v for k, v in case.items() if k != "expected"}
    stripped["policy_id"] = stripped.pop("policy_no")
    return stripped


def _final_decision_for(expected: dict[str, Any]) -> str:
    liability = expected.get("liability")
    if liability == "not_covered":
        return "rejected"
    if liability == "partial":
        return "partial"
    return "approved"


def _case_by_id(case_id: str) -> dict[str, Any]:
    return next(c for c in CASES if c["case_id"] == case_id)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
async def test_gold_sample_route_and_amount(case: dict[str, Any]) -> None:
    graph, recorder = _graph()
    case_id = case["case_id"]
    expected = case["expected"]
    config = {"configurable": {"thread_id": case_id}}

    result = await graph.ainvoke(_input(case), config)
    state = graph.get_state(config).values

    if expected["route"] == "auto":
        assert "__interrupt__" not in result, f"{case_id} 应自动签发而非挂起"
        assert state["final_decision"] == _final_decision_for(expected)
        assert Decimal(str(result["approved_amount"])) == Decimal(expected["approved_amount"])
        assert result["decision_document"]["body"]
        # 预算与审计：调度次数在预算内，七个阶段结论全部落审计
        assert state["routing_calls"] <= 15
        kinds = {e["kind"] for e in recorder.events if e["case_id"] == case_id}
        assert {"routing", "stage_result", "status_change"} <= kinds
        assert {
            e["stage"]
            for e in recorder.events
            if e["case_id"] == case_id and e["kind"] == "stage_result"
        } >= {"intake", "material_review", "policy_verify", "fraud_check",
              "liability_judge", "amount_calc", "decision_generate", "compliance_gate"}
        final_update = [
            u for u in recorder.updates if u.get("status") == "auto_issued"
            and u["case_id"] == case_id
        ]
        assert final_update, "自动签发必须落案件状态"
    elif expected["route"] == "human":
        assert result.get("__interrupt__"), f"{case_id} 应转人工挂起"
        payload = result["__interrupt__"][0].value
        want_kind = "escape" if case_id in ("CASE-2026-0021", "CASE-2026-0022") else "review"
        assert payload["kind"] == want_kind
        if case_id in ("CASE-2026-0021", "CASE-2026-0022"):
            assert state["case_type"] in ("accident", "unknown")
        elif expected["approved_amount"] is not None:
            # 带金额期望的转人工案（超阈值/封顶）：理算已完成且金额精确
            assert Decimal(str(state["calc"]["approved_amount"])) == Decimal(
                expected["approved_amount"]
            )
        assert state.get("final_decision") is None
    else:  # supplement
        assert result.get("__interrupt__"), f"{case_id} 应补件挂起"
        payload = result["__interrupt__"][0].value
        assert payload["kind"] == "supplement"
        assert sorted(payload["missing"]) == sorted(EXPECTED_MISSING[case_id])


async def test_supplement_resume_completes_e2e() -> None:
    """补件闭环：挂起 → Command(resume) 补传材料 → 重跑材料审核 → 自动签发 4640。"""
    case = _case_by_id("CASE-2026-0023")
    graph, recorder = _graph()
    config = {"configurable": {"thread_id": "CASE-2026-0023"}}

    interrupted = await graph.ainvoke(_input(case), config)
    assert interrupted.get("__interrupt__")
    assert interrupted["__interrupt__"][0].value["missing"] == ["费用清单"]

    resumed = await graph.ainvoke(
        Command(
            resume={
                "kind": "supplement",
                "resolved_by": "customer",
                "added_materials": [
                    {"file_name": "cost_list_new.pdf", "doc_type": "cost_list"}
                ],
            }
        ),
        config,
    )
    # 带输出 schema 的图在恢复运行返回值中会携带已消费的 __interrupt__ 历史，
    # 终态断言看业务字段 + 图是否已走完（无待恢复任务）
    assert resumed["final_decision"] == "approved"
    assert Decimal(str(resumed["approved_amount"])) == Decimal("4640.00")
    assert graph.get_state(config).next == ()
    # 补件材料已合并进案件事实（续跑后材料清单含新文件）
    state = graph.get_state(config).values
    assert any(m["file_name"] == "cost_list_new.pdf" for m in state["materials"])
    # 审计链：材料审核跑了两轮（首次缺件 + 补件后重跑）
    material_events = [
        e for e in recorder.events
        if e["case_id"] == "CASE-2026-0023"
        and e["kind"] == "stage_result"
        and e["stage"] == "material_review"
    ]
    assert len(material_events) == 2


async def test_review_resume_minimal_closure() -> None:
    """签批类挂起（T079 最小闭环）：恢复即转人工终态（T086 接坐席结论 + 合规复审）。"""
    case = _case_by_id("CASE-2026-0007")
    graph, _ = _graph()
    config = {"configurable": {"thread_id": "CASE-2026-0007"}}

    interrupted = await graph.ainvoke(_input(case), config)
    assert interrupted.get("__interrupt__")
    resumed = await graph.ainvoke(
        Command(resume={"kind": "review", "resolved_by": "agent-01"}), config
    )
    assert resumed["final_decision"] == "referred"


def test_static_compliance_gate_not_bypassable() -> None:
    """D039 安全设计 1（图结构断言）：decision_generate → compliance_gate 为静态边，
    orchestrator 无任何静态出边（其派发只能经 Command(goto)，受守卫约束）。

    断言看 builder.edges（真实接线）而非 get_graph()（可视化视图对 Command 节点不可靠）。
    """
    graph, _ = _graph()
    edges: set[tuple[str, str]] = set()
    for e in graph.builder.edges:  # langgraph 版本间为 tuple 或 Edge 对象
        edges.add((e[0], e[1]) if isinstance(e, tuple) else (e.source, e.target))
    assert ("decision_generate", "compliance_gate") in edges
    assert not any(src == "orchestrator" for src, _ in edges), (
        "orchestrator 不得有静态出边（派发必须走守卫）"
    )
    assert ("revise_decision", "compliance_gate") in edges
    for worker in (
        "material_review",
        "policy_verify",
        "fraud_check",
        "liability_judge",
        "amount_calc",
    ):
        assert (worker, "orchestrator") in edges
