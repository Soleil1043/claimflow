"""保单核验与风控节点测试（T083）：等待期/除外/限额核验逻辑 + 金样本信号。"""

from __future__ import annotations

from typing import Any

from nodes.fraud_check import make_fraud_check_node
from nodes.guards import default_route
from nodes.policy_verify import make_policy_verify_node


class MiniRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append({"case_id": case_id, "kind": kind, "stage": stage, "payload": payload})

    async def update_case(self, case_id, **kwargs) -> None:
        pass

    async def save_decision(self, case_id, **kwargs) -> None:
        pass


def _policy(**overrides) -> dict[str, Any]:
    base = {
        "policy_no": "POL-2025-0001",
        "holder_id_card": "330106199203154817",
        "product_type": "医疗险",
        "status": "active",
        "coverage_amount": "1000000.00",
        "deductible": "10000.00",
        "payout_ratio": "0.8000",
        "effective_date": "2025-01-01",
        "expiry_date": "2026-12-31",
        "terms": {
            "waiting_period_days": 30,
            "exclusions": ["整形美容", "牙科", "矫正", "先天性疾病", "既往症"],
            "coverage_scope": ["疾病住院医疗", "住院手术"],
            "limit_notes": "累计赔付不超过保额",
        },
    }
    base.update(overrides)
    return base


def _state(incident_date) -> dict[str, Any]:
    return {
        "case_id": "C1",
        "policy_id": "POL-2025-0001",
        "incident_date": incident_date,
    }


async def _verify(policy: dict | None, incident_date) -> dict[str, Any]:
    async def lookup(policy_no):
        return policy

    node = make_policy_verify_node(MiniRecorder(), lookup)
    update = await node(_state(incident_date))
    return update["policy"]


# ---------- 等待期边界（验收：等待期核验逻辑单测） ----------


async def test_waiting_period_day_29_fails() -> None:
    """生效日 2026-08-01：第 29 天出险 → 等待期未过。"""
    policy = _policy(
        effective_date="2026-08-01", expiry_date="2027-07-31"
    )
    result = await _verify(policy, "2026-08-30")
    assert result["waiting_period_passed"] is False
    assert result["coverage_valid"] is True  # 在保障期内，仅等待期未过
    assert "等待期" in (result["invalid_reason"] or "")


async def test_waiting_period_last_day_fails() -> None:
    """契约口径（T122 对齐）：出险日 = 生效日+30（等待期最后一日）→ 仍未过。"""
    policy = _policy(effective_date="2026-08-01", expiry_date="2027-07-31")
    result = await _verify(policy, "2026-08-31")
    assert result["waiting_period_passed"] is False
    assert result["coverage_valid"] is True  # 在保障期内，仅等待期未过


async def test_waiting_period_first_payable_day_passes() -> None:
    """首个可赔日 = 生效日+31（第 31 天，金样本 offset 计数口径）→ 等待期已过。"""
    policy = _policy(effective_date="2026-08-01", expiry_date="2027-07-31")
    result = await _verify(policy, "2026-09-01")
    assert result["waiting_period_passed"] is True


async def test_waiting_period_day_32_passes() -> None:
    """第 32 天 → 等待期已过（金样本 CASE-2026-0003 口径）。"""
    policy = _policy(effective_date="2026-08-01", expiry_date="2027-07-31")
    result = await _verify(policy, "2026-09-01")
    assert result["waiting_period_passed"] is True


async def test_waiting_days_from_terms_override() -> None:
    """等待期天数由条款要素驱动（terms 覆盖默认 30 天）。"""
    policy = _policy(
        effective_date="2026-08-01",
        expiry_date="2027-07-31",
        terms={"waiting_period_days": 90, "exclusions": [], "coverage_scope": []},
    )
    result = await _verify(policy, "2026-09-01")  # 第 32 天：30 天口径已过、90 天未过
    assert result["waiting_period_passed"] is False


# ---------- 有效性 / 限额回填 ----------


async def test_expired_policy_invalid() -> None:
    policy = _policy(status="expired")
    result = await _verify(policy, "2026-06-30")
    assert result["coverage_valid"] is False
    assert "状态" in (result["invalid_reason"] or "")


async def test_out_of_term_invalid() -> None:
    policy = _policy(effective_date="2026-01-01", expiry_date="2026-02-28")
    result = await _verify(policy, "2026-06-30")
    assert result["coverage_valid"] is False
    assert "保障期" in (result["invalid_reason"] or "")


async def test_policy_not_found() -> None:
    result = await _verify(None, "2026-06-30")
    assert result["policy_found"] is False
    assert result["coverage_valid"] is False


async def test_terms_amounts_backfilled() -> None:
    """理算三要素（保额/免赔/比例）+ 保障范围/除外由条款要素回填。"""
    result = await _verify(_policy(), "2026-08-10")
    assert result["policy_amount"] == "1000000.00"
    assert result["deductible"] == "10000.00"
    assert result["payout_ratio"] == "0.8000"
    assert "整形美容" in result["exclusions"]
    assert result["coverage_scope"] == ["疾病住院医疗", "住院手术"]


# ---------- 风控节点（验收：金样本信号 → 期望等级） ----------


async def test_fraud_blacklist_high_short_circuit_signal() -> None:
    """CASE-2026-0015 口径：黑名单 → high；default_route 高风险短路 → human。"""
    rec = MiniRecorder()

    async def lookup(state):
        return {"blacklisted": True, "recent_claims": 0}

    node = make_fraud_check_node(rec, lookup)
    update = await node({"case_id": "C1", "user_id": "u-zhaomin", "policy_id": "P"})
    assert update["risk"]["risk_level"] == "high"
    assert update["risk"]["blacklisted"] is True

    # 高风险短路：orchestrator 在 risk 就绪后必须转人工而非继续理算
    state = {
        "material": {"completeness": "complete", "missing": [], "confidence": 1.0},
        "policy": {"x": 1},
        "risk": {"risk_level": "high"},
    }
    targets, human = default_route(state)
    assert targets == ["human_gate"] and human["kind"] == "review"


async def test_fraud_high_frequency_medium() -> None:
    """CASE-2026-0016 口径：近 90 天 2 次 → medium（流程继续，签发阶段转人工）。"""
    async def lookup(state):
        return {"blacklisted": False, "recent_claims": 2}

    node = make_fraud_check_node(MiniRecorder(), lookup)
    update = await node({"case_id": "C1", "user_id": "u-sunqiang", "policy_id": "P"})
    assert update["risk"]["risk_level"] == "medium"
    assert update["risk"]["recent_claims_count"] == 2

    # medium 不短路：orchestrator 继续责任认定
    state = {
        "material": {"completeness": "complete", "missing": [], "confidence": 1.0},
        "policy": {"x": 1},
        "risk": {"risk_level": "medium"},
    }
    targets, _ = default_route(state)
    assert targets == ["liability_judge"]


async def test_fraud_clean_low() -> None:
    async def lookup(state):
        return None

    node = make_fraud_check_node(MiniRecorder(), lookup)
    update = await node({"case_id": "C1", "user_id": "u", "policy_id": "P"})
    assert update["risk"]["risk_level"] == "low"
    assert update["risk"]["risk_score"] == 5.0
