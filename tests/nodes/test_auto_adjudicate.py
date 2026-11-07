"""分级自动签发节点测试（T206/I3 方案 A，D089）：拒赔同样受风险与置信度约束。

此前 not_covered 以 `not not_covered and` 前缀跳过风险与置信度检查——高频/黑名单
申请人、低置信认定都直接自动出拒赔书（与 spec F11"低风险+责任明确"相悖）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

import nodes.auto_adjudicate as aa_module
from nodes.auto_adjudicate import adjudication_route, make_auto_adjudicate_node


class _Recorder:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        pass

    async def update_case(self, case_id, **kwargs) -> None:
        self.updates.append(kwargs)

    async def save_decision(self, case_id, **kwargs) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(aa_module, "write_case_memory", _noop)


def _state(*, verdict: str, risk: str = "low", confidence: float = 0.95,
           amount: str = "0.00") -> dict[str, Any]:
    return {
        "case_id": "CASE-T206",
        "case_type": "medical",
        "risk": {"risk_level": risk},
        "liability": {"verdict": verdict, "confidence": confidence},
        "material": {"confidence": 1.0},
        "calc": {"approved_amount": amount},
        "decision": {"conclusion": "rejected", "body": "x"},
    }


async def _run(state: dict[str, Any]) -> dict[str, Any]:
    return await make_auto_adjudicate_node(_Recorder())(state)


async def test_low_risk_confident_rejection_auto_issued() -> None:
    update = await _run(_state(verdict="not_covered"))
    assert adjudication_route(update) == "issue"
    assert update["final_decision"] == "rejected"
    assert update["approved_amount"] == Decimal("0.00")


@pytest.mark.parametrize("risk", ["medium", "high"])
async def test_risky_rejection_referred(risk: str) -> None:
    update = await _run(_state(verdict="not_covered", risk=risk))
    assert adjudication_route(update) == "human"
    assert f"风险等级 {risk}" in update["human_request"]["reason"]


async def test_low_confidence_rejection_referred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aa_module.settings, "auto_approve_confidence_floor", 0.8)
    update = await _run(_state(verdict="not_covered", confidence=0.5))
    assert adjudication_route(update) == "human"
    assert "置信度" in update["human_request"]["reason"]


async def test_amount_limit_not_applied_to_rejection() -> None:
    """金额线对拒赔不适用（核定恒 0，即使理算残留大额也自动拒赔）。"""
    update = await _run(_state(verdict="not_covered", amount="99999.00"))
    assert adjudication_route(update) == "issue"
    assert update["approved_amount"] == Decimal("0.00")


async def test_covered_over_limit_still_referred() -> None:
    update = await _run(_state(verdict="covered", amount="99999.00"))
    assert adjudication_route(update) == "human"
    assert "自动签发线" in update["human_request"]["reason"]
