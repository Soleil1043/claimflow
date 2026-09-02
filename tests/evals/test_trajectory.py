"""T050 轨迹质量判分测试：LCS 顺序 / 禁调 / 路由 / 次数冗余 / 入参断言 + 聚合口径。

D026 独立口径：轨迹分项不并入 passed，聚合分母只计标注用例。
"""

from __future__ import annotations

from evals.metrics import CaseResult, aggregate, result_from_a06, score_case
from evals.schemas import EvalCase, EvalCategory
from evals.trajectory import count_redundant, lcs_length, score_trajectory


def _case(**kwargs: object) -> EvalCase:
    base = {
        "id": "T-001",
        "category": EvalCategory.MULTI_STEP,
        "user_input": "测试输入",
        "must_include": ["4640"],
    }
    base.update(kwargs)  # type: ignore[arg-type]
    return EvalCase.model_validate(base)


def _result(**kwargs: object) -> CaseResult:
    base = {"case_id": "T-001", "category": EvalCategory.MULTI_STEP}
    base.update(kwargs)  # type: ignore[arg-type]
    return CaseResult.model_validate(base)


def _trace(*calls: tuple[str, dict | None]) -> list[dict]:
    """按序构造轨迹摘要。"""
    return [{"agent": "claim", "tool": tool, "input": args or {}} for tool, args in calls]


# ===== lcs_length =====


def test_lcs_length_basics() -> None:
    assert lcs_length([], ["a"]) == 0
    assert lcs_length(["a", "b"], ["a", "b"]) == 2
    # 期望序列是实际序列的子序列（中间有插入调用）→ LCS = 期望长度
    assert lcs_length(["a", "c"], ["a", "b", "c"]) == 2
    # 乱序 → 只命中公共部分
    assert lcs_length(["c", "a"], ["a", "b", "c"]) == 1


# ===== 顺序维度 =====


def test_order_tolerates_extra_calls() -> None:
    """先查保单后计算：中间多一次 RAG 不影响顺序命中。"""
    case = _case(expected_tool_order=["policy_query", "claim_calculator"])
    r = score_trajectory(
        case,
        _result(
            tool_trace=_trace(
                ("policy_query", None), ("claim_rule_rag", None), ("claim_calculator", None)
            )
        ),
    )
    assert r.order_match and r.order_ratio == 1.0
    assert r.trajectory_expectations == {"order": True}


def test_order_disorder_fails() -> None:
    case = _case(expected_tool_order=["policy_query", "claim_calculator"])
    r = score_trajectory(
        case, _result(tool_trace=_trace(("claim_calculator", None), ("policy_query", None)))
    )
    assert not r.order_match and r.order_ratio < 1.0


# ===== 禁调维度 =====


def test_forbidden_violation_detected() -> None:
    case = _case(forbidden_tools=["claim_calculator"])
    r = score_trajectory(
        case, _result(tool_trace=_trace(("policy_query", None), ("claim_calculator", None)))
    )
    assert not r.forbidden_clean


def test_forbidden_clean() -> None:
    case = _case(forbidden_tools=["claim_calculator"])
    r = score_trajectory(case, _result(tool_trace=_trace(("policy_query", None))))
    assert r.forbidden_clean


# ===== 路由维度 =====


def test_route_subsequence_match() -> None:
    """期望路由是实际路由的按序子序列即通过（执行中重规划多投一次可容忍）。"""
    case = _case(expected_route=["claim", "medical"])
    r = score_trajectory(case, _result(agent_route=["claim", "medical", "claim"]))
    assert r.route_match


def test_route_mismatch() -> None:
    case = _case(expected_route=["medical", "claim"])
    r = score_trajectory(case, _result(agent_route=["claim", "medical"]))
    assert not r.route_match


# ===== 次数与冗余 =====


def test_max_tool_calls_over_limit() -> None:
    case = _case(max_tool_calls=2)
    r = score_trajectory(
        case,
        _result(
            tool_trace=_trace(
                ("policy_query", None), ("claim_rule_rag", None), ("claim_calculator", None)
            )
        ),
    )
    assert not r.calls_within_limit


def test_count_redundant_same_tool_same_args() -> None:
    trace = _trace(
        ("policy_query", {"policy_no": "P1"}),
        ("policy_query", {"policy_no": "P1"}),  # 同参重复 → 冗余
        ("policy_query", {"policy_no": "P2"}),  # 异参不算
    )
    assert count_redundant(trace) == 1


def test_no_expectations_no_scores() -> None:
    """未标注任何轨迹维度：不写 expectations，分项保持默认通过。"""
    r = score_trajectory(_case(), _result(tool_trace=_trace(("policy_query", None))))
    assert r.trajectory_expectations == {}
    assert r.order_match and r.forbidden_clean and r.route_match and r.calls_within_limit
    assert r.redundant_calls == 0


# ===== 入参断言 =====


def test_args_subset_match_with_norm() -> None:
    """关键入参子集命中（归一化容忍空白），多余入参不考核。"""
    case = _case(expected_tool_args={"policy_query": {"policy_no": "POL-2025-0001"}})
    r = score_trajectory(
        case,
        _result(tool_trace=_trace(("policy_query", {"policy_no": "POL-2025-0001", "extra": "x"}))),
    )
    assert r.args_match


def test_args_value_normalized() -> None:
    case = _case(expected_tool_args={"policy_query": {"policy_no": "POL-2025-0001"}})
    r = score_trajectory(
        case, _result(tool_trace=_trace(("policy_query", {"policy_no": "POL - 2025 - 0001"})))
    )
    assert r.args_match


def test_args_wrong_value_and_missing_tool() -> None:
    case = _case(expected_tool_args={"policy_query": {"policy_no": "POL-2025-0001"}})
    bad = score_trajectory(
        case, _result(tool_trace=_trace(("policy_query", {"policy_no": "POL-9999-9999"})))
    )
    assert not bad.args_match
    missing = score_trajectory(case, _result(tool_trace=_trace(("claim_calculator", None))))
    assert not missing.args_match


# ===== 与 passed 的关系（D026 核心） =====


def test_trajectory_violation_does_not_fail_passed() -> None:
    """答案判分全过 + 轨迹违规 → passed 仍为 True，仅轨迹分项记录违规。"""
    case = _case(
        must_include=["4640"],
        expected_tool_order=["policy_query", "claim_calculator"],
        forbidden_tools=["claim_rule_rag"],
        max_tool_calls=5,
    )
    r = score_case(
        case,
        _result(
            answer="扣除免赔后可赔 4640 元",
            used_tools=["policy_query", "claim_calculator"],
            tool_trace=_trace(
                ("claim_calculator", None),  # 乱序
                ("claim_rule_rag", None),  # 禁调
            ),
        ),
    )
    assert r.passed
    assert not r.order_match and not r.forbidden_clean
    assert r.trajectory_expectations == {"order": True, "forbidden": True, "limit": True}


# ===== 聚合口径 =====


def test_aggregate_trajectory_rates_scored_denominator() -> None:
    """分母只计标注用例；未标注维度 rate=1.0 / scored=0。"""
    annotated_ok = score_trajectory(
        _case(id="A", expected_tool_order=["policy_query", "claim_calculator"]),
        _result(case_id="A", tool_trace=_trace(("policy_query", None), ("claim_calculator", None))),
    )
    annotated_bad = score_trajectory(
        _case(id="B", expected_tool_order=["policy_query", "claim_calculator"]),
        _result(case_id="B", tool_trace=_trace(("claim_calculator", None))),
    )
    plain = score_trajectory(
        _case(id="C"),
        _result(case_id="C", tool_trace=_trace(("policy_query", None), ("policy_query", None))),
    )

    report = aggregate([annotated_ok, annotated_bad, plain])
    assert report.trajectory["order"] == {"rate": 0.5, "scored": 2.0}
    assert report.trajectory["route"]["scored"] == 0.0
    assert report.trajectory["route"]["rate"] == 1.0
    # 未标注维度不考核，但冗余计数照常统计（有轨迹的用例都计入）
    assert report.trajectory["redundancy"]["cases_with_trace"] == 3.0
    assert report.trajectory["redundancy"]["avg_redundant_calls"] == round(1 / 3, 2)


# ===== result_from_a06 适配 =====


def test_result_from_a06_trace_digest_strips_output() -> None:
    case = _case()
    a06 = {
        "answer": "可赔 4640 元",
        "tool_trace": [
            {
                "agent": "claim",
                "tool": "policy_query",
                "input": {"policy_no": "P1"},
                "output": {"data": {"x": 1}},
            },
            {
                "agent": "claim",
                "tool": "claim_calculator",
                "input": {"medical_expense": 1},
                "output": {},
            },
        ],
        "agent_route": ["claim"],
        "used_tools": ["policy_query", "claim_calculator"],
    }
    r = result_from_a06(case, a06, duration_s=1.0)
    assert r.used_tools == ["policy_query", "claim_calculator"]
    assert r.tool_trace == [
        {"agent": "claim", "tool": "policy_query", "input": {"policy_no": "P1"}},
        {"agent": "claim", "tool": "claim_calculator", "input": {"medical_expense": 1}},
    ]
    assert r.agent_route == ["claim"]


def test_result_from_a06_fallback_to_used_tools() -> None:
    """无 tool_trace 键时回退 used_tools（旧 A06 结构兼容）。"""
    case = _case()
    a06 = {
        "answer": "ok",
        "used_tools": [{"tool": "policy_query", "input": {}, "output": {}}],
    }
    r = result_from_a06(case, a06, duration_s=1.0)
    assert r.used_tools == ["policy_query"]
    assert r.tool_trace == [{"agent": "", "tool": "policy_query", "input": {}}]
