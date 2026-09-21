"""叙述抽评采样测试（T139，缺口#4）：确定性采样 + 节点级事件落库。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

import nodes.auto_adjudicate as aa
from nodes.auto_adjudicate import _narrative_sampled, make_auto_adjudicate_node


class _StubRecorder:
    """记录事件的桩 recorder（CaseRecorder 协议最小面）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any] | None]] = []

    async def event(
        self,
        case_id: str,
        kind: str,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((case_id, kind, payload))

    async def update_case(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        return None


def test_sampling_deterministic_per_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """确定性：同案同结果（可复算），不同案分布有差异，rate=0 关闭 / rate=1 全采。

    注意 patch 消费方（aa.settings）而非函数内 import——test_logging 会 reload
    app.core.config 产生新单例（settings 双实例陷阱，conftest 口径）。
    """
    monkeypatch.setattr(aa.settings, "manual_review_sample_rate", 0.05)
    assert _narrative_sampled("CASE-2026-0001") == _narrative_sampled("CASE-2026-0001")
    # 1000 案中两类都出现（0.05 采样下确定性种子应命中两侧）
    outcomes = {_narrative_sampled(f"CASE-{i:04d}") for i in range(1000)}
    assert outcomes == {True, False}

    monkeypatch.setattr(aa.settings, "manual_review_sample_rate", 0.0)
    assert not _narrative_sampled("CASE-2026-0001")
    monkeypatch.setattr(aa.settings, "manual_review_sample_rate", 1.0)
    assert _narrative_sampled("CASE-2026-0001")


async def test_node_emits_sample_event_when_sampled(monkeypatch: pytest.MonkeyPatch) -> None:
    """rate=1 时签发分支落 narrative_sample 事件；rate=0 时零采样事件。"""
    state = {
        "case_id": "CASE-2026-0042",
        "user_id": "u-1",
        "case_type": "medical",
        "claimed_amount": Decimal("15800.00"),
        "material": {"confidence": 0.9},
        "liability": {"verdict": "covered", "confidence": 0.9},
        "risk": {"risk_level": "low"},
        "calc": {"approved_amount": "4640.00"},
        "decision": {},
    }

    monkeypatch.setattr(aa.settings, "manual_review_sample_rate", 1.0)
    recorder = _StubRecorder()
    node = make_auto_adjudicate_node(recorder)  # type: ignore[arg-type]
    result = await node(state)  # type: ignore[arg-type]
    assert result["final_decision"] == "approved"
    kinds = [k for _, k, _ in recorder.events]
    assert "narrative_sample" in kinds

    monkeypatch.setattr(aa.settings, "manual_review_sample_rate", 0.0)
    recorder0 = _StubRecorder()
    node0 = make_auto_adjudicate_node(recorder0)  # type: ignore[arg-type]
    await node0({**state, "case_id": "CASE-2026-0043"})  # type: ignore[arg-type]
    kinds0 = [k for _, k, _ in recorder0.events]
    assert "narrative_sample" not in kinds0
    assert "status_change" in kinds0  # 签发路径本身不受采样影响
