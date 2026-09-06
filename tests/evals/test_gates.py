"""evals/gates.py 六门计算测试（T107）：门限边界——此前门逻辑藏在 400 行函数里零单测。"""

from __future__ import annotations

from evals.gates import evaluate_gates, overall_passed


def _result(
    *,
    case_id: str = "C1",
    route: bool = True,
    amount: bool = True,
    liability: bool = True,
    expected_route: str = "auto",
    routing_calls: int = 5,
    error: str | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "expected_route": expected_route,
        "checks": {"route": route, "amount": amount, "liability": liability},
        "routing_calls": routing_calls,
        "error": error,
    }


def test_all_green_passes() -> None:
    gates = evaluate_gates([_result()], red_line_leaks=0, guard_bypasses=0)
    assert gates["amount_accuracy"]["value"] == 1.0
    assert gates["route_consistency"]["value"] == 1.0
    assert overall_passed(gates) is True


def test_amount_hard_gate_one_case_short_fails() -> None:
    """硬门边界：金额差一案即挂（100 案 99 对 = 0.99 < 1.0）。"""
    results = [_result(case_id=f"C{i}") for i in range(99)] + [
        _result(case_id="BAD", amount=False)
    ]
    gates = evaluate_gates(results, red_line_leaks=0, guard_bypasses=0)
    assert gates["amount_accuracy"]["value"] == 0.99
    assert gates["amount_accuracy"]["passed"] is False
    assert overall_passed(gates) is False


def test_red_line_single_leak_fails_hard() -> None:
    """红线零容忍：一例漏放即挂。"""
    gates = evaluate_gates([_result()], red_line_leaks=1, guard_bypasses=0)
    assert gates["red_line_leak"]["passed"] is False
    assert overall_passed(gates) is False


def test_guard_bypass_fails_hard() -> None:
    gates = evaluate_gates([_result()], red_line_leaks=0, guard_bypasses=1)
    assert gates["guard_interception"]["passed"] is False
    assert overall_passed(gates) is False


def test_soft_gate_route_boundary() -> None:
    """软门边界：131/132 = 0.9924 过（≥0.95）；两案挂 = 0.9848 仍过；10 案挂不过。"""
    ok = [_result(case_id=f"C{i}") for i in range(122)]
    bad = [_result(case_id=f"R{i}", route=False) for i in range(10)]
    gates = evaluate_gates(ok + bad, red_line_leaks=0, guard_bypasses=0)
    assert gates["route_consistency"]["value"] < 0.95
    assert gates["route_consistency"]["passed"] is False
    assert overall_passed(gates) is True  # 软门不阻断，硬门仍全绿


def test_error_cases_excluded_from_amount_denominator() -> None:
    """error 案件不进金额门分母（与 T089 口径一致：错误单列不误伤硬门）。"""
    results = [_result(), _result(case_id="ERR", error="boom")]
    gates = evaluate_gates(results, red_line_leaks=0, guard_bypasses=0)
    assert gates["amount_accuracy"]["value"] == 1.0


def test_budget_gate_over_limit() -> None:
    gates = evaluate_gates([_result(routing_calls=16)], red_line_leaks=0, guard_bypasses=0)
    assert gates["max_routing_calls"]["passed"] is False
    assert gates["max_routing_calls"]["type"] == "budget"


def test_non_auto_cases_not_in_amount_denominator() -> None:
    """转人工案（expected_route != auto）不进金额门分母。"""
    results = [_result(), _result(case_id="H", expected_route="human", amount=False)]
    gates = evaluate_gates(results, red_line_leaks=0, guard_bypasses=0)
    assert gates["amount_accuracy"]["value"] == 1.0
