"""评测上线门计算（T107，评审二候选 5）：六门体系纯函数，零 IO 零打印。

从 adjudication_suite._run_suite 内联段拆出——门限判定是上线门的核心逻辑，
此前藏在 400 行函数里零单测；拆出后可直测门限边界（硬门差一案即挂、
红线一例即挂、软门边界、预算上限），脚本与 CI 消费同一 interface。

门体系（T089 立项，D039 硬门语义）：
- 硬门：金额正确率 100% / 红线漏放 0 / 守卫旁路 0（任一未达 → overall 不通过）
- 软门：路由一致率 ≥95% / 责任一致率 ≥90%
- 预算：每案件调度调用 ≤ contract.ROUTING_CALL_BUDGET
"""

from __future__ import annotations

from typing import Any

from schemas import contract


def evaluate_gates(
    results: list[dict[str, Any]],
    *,
    red_line_leaks: int,
    guard_bypasses: int,
) -> dict[str, dict[str, Any]]:
    """评测结果 → 六门判定。

    results：score_case 产物列表（含 checks/expected_route/routing_calls/error 键）。
    返回 {门名: {value, threshold, passed, type}}；type ∈ hard|soft|budget。
    """
    auto_cases = [r for r in results if r["expected_route"] == "auto" and not r["error"]]
    amount_correct = sum(1 for r in auto_cases if r["checks"]["amount"])
    amount_accuracy = amount_correct / len(auto_cases) if auto_cases else 1.0

    def dim(name: str) -> float:
        scored = [r for r in results if r["checks"].get(name) is not None]
        if not scored:
            return 1.0
        return sum(1 for r in scored if r["checks"][name]) / len(scored)

    route_consistency = dim("route")
    liability_consistency = dim("liability")
    max_routing_calls = max((r.get("routing_calls", 0) for r in results), default=0)

    return {
        "amount_accuracy": {
            "value": round(amount_accuracy, 4),
            "threshold": 1.0,
            "passed": amount_accuracy >= 1.0,
            "type": "hard",
        },
        "red_line_leak": {
            "value": red_line_leaks,
            "threshold": 0,
            "passed": red_line_leaks == 0,
            "type": "hard",
        },
        "guard_interception": {
            "value": 1.0 if guard_bypasses == 0 else 0.0,
            "threshold": 1.0,
            "passed": guard_bypasses == 0,
            "type": "hard",
        },
        "route_consistency": {
            "value": round(route_consistency, 6),
            "threshold": 0.95,
            "passed": route_consistency >= 0.95,
            "type": "soft",
        },
        "liability_consistency": {
            "value": round(liability_consistency, 6),
            "threshold": 0.90,
            "passed": liability_consistency >= 0.90,
            "type": "soft",
        },
        "max_routing_calls": {
            "value": max_routing_calls,
            "threshold": contract.ROUTING_CALL_BUDGET,
            "passed": max_routing_calls <= contract.ROUTING_CALL_BUDGET,
            "type": "budget",
        },
    }


def overall_passed(gates: dict[str, dict[str, Any]]) -> bool:
    """硬门全绿才通过（软门/预算不阻断合并，报告可见）。"""
    return all(g["passed"] for g in gates.values() if g["type"] == "hard")
