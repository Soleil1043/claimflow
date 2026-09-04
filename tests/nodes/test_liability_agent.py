"""责任认定节点单元测试（T084）：确定性前置 / 结果映射 / 关键词兜底 / skill 装配。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nodes.liability_judge import (
    LiabilityOutput,
    _build_agent_def,
    keyword_only_invoker,
    make_liability_judge_node,
)


class MiniRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append({"case_id": case_id, "kind": kind, "stage": stage, "payload": payload})

    async def update_case(self, case_id, **kwargs) -> None:
        pass

    async def save_decision(self, case_id, **kwargs) -> None:
        pass


def _state(**overrides) -> dict:
    base = {
        "case_id": "C1",
        "incident_description": "急性阑尾炎住院手术，共花费15800元。",
        "policy": {
            "coverage_valid": True,
            "waiting_period_passed": True,
            "invalid_reason": None,
            "exclusions": ["整形美容"],
        },
        "material": {"documents": []},
    }
    base.update(overrides)
    return base


async def test_policy_invalid_precheck_skips_llm() -> None:
    """保单无效 → 确定性前置直接 not_covered，LLM 不被调用（D039 口径）。"""
    calls: list[int] = []

    async def spy_invoker(agent_def, instruction, shared_data):
        calls.append(1)
        return {"verdict": "covered"}, []

    node = make_liability_judge_node(MiniRecorder(), invoker=spy_invoker)
    update = await node(
        _state(
            policy={
                "coverage_valid": False,
                "waiting_period_passed": None,
                "invalid_reason": "保单状态为 expired，保障已终止",
            }
        )
    )
    assert calls == []
    assert update["liability"]["verdict"] == "not_covered"
    assert "expired" in update["liability"]["reason"]


async def test_waiting_period_precheck() -> None:
    """等待期未过 → 确定性前置 not_covered，不经 LLM。"""
    calls: list[int] = []

    async def spy_invoker(agent_def, instruction, shared_data):
        calls.append(1)
        return {"verdict": "covered"}, []

    node = make_liability_judge_node(MiniRecorder(), invoker=spy_invoker)
    update = await node(
        _state(
            policy={
                "coverage_valid": True,
                "waiting_period_passed": False,
                "invalid_reason": "出险日在等待期（30 天）内",
            }
        )
    )
    assert calls == []
    assert update["liability"]["verdict"] == "not_covered"
    assert update["liability"]["clause_references"] == ["第五条 责任免除"]


async def test_invoker_result_mapped_and_traced() -> None:
    """LLM 结论映射进 liability channel；tools_used 审计来自轨迹派生。"""

    async def fake_invoker(agent_def, instruction, shared_data):
        assert "急性阑尾炎" in instruction  # 案件事实注入指令
        return (
            {
                "verdict": "covered",
                "reason": "急性阑尾炎手术属住院医疗责任",
                "clause_references": ["第三条 保险责任"],
                "confidence": 0.92,
            },
            [],
        )

    rec = MiniRecorder()
    node = make_liability_judge_node(rec, invoker=fake_invoker)
    update = await node(_state())
    assert update["liability"]["verdict"] == "covered"
    assert Decimal(str(update["liability"]["confidence"])) == Decimal("0.92")
    event = next(e for e in rec.events if e["stage"] == "liability_judge")
    assert event["payload"]["tools_used"] == []


async def test_invoker_failure_falls_back_covered() -> None:
    """LLM 失败 → 关键词兜底：清洁描述 → covered（置信度 0.9 过签发门槛）。"""

    async def boom(agent_def, instruction, shared_data):
        raise RuntimeError("LLM 不可用")

    node = make_liability_judge_node(MiniRecorder(), invoker=boom)
    update = await node(_state())
    assert update["liability"]["verdict"] == "covered"
    assert Decimal(str(update["liability"]["confidence"])) == Decimal("0.9")


async def test_fallback_exclusion_not_covered() -> None:
    """兜底规则：整形 → not_covered + 除外触发。"""
    node = make_liability_judge_node(MiniRecorder(), invoker=keyword_only_invoker)
    update = await node(
        _state(
            incident_description="鼻综合整形手术，费用23000元。",
            policy={"coverage_valid": True, "waiting_period_passed": True},
        )
    )
    assert update["liability"]["verdict"] == "not_covered"
    assert update["liability"]["exclusions_triggered"] == ["整形美容"]


async def test_fallback_self_pay_partial() -> None:
    """兜底规则：自费金额识别 → partial + self_pay_amount。"""
    node = make_liability_judge_node(MiniRecorder(), invoker=keyword_only_invoker)
    update = await node(
        _state(
            incident_description="骨科手术，含自费内固定材料2500元，总费用17500元。",
        )
    )
    assert update["liability"]["verdict"] == "partial"
    assert Decimal(str(update["liability"]["self_pay_amount"])) == Decimal("2500")


def test_skill_assembled_into_system_prompt() -> None:
    """skill 装配断言：责任认定规程内容进入 system prompt。"""
    agent_def = _build_agent_def()
    assert "责任免除" in agent_def.system_prompt
    assert "{case_facts}" not in agent_def.system_prompt  # 占位符已由调用方填充


def test_output_schema_is_stage_model() -> None:
    """response_format 直接复用阶段模型 LiabilityOutput（无重复定义）。"""
    assert _build_agent_def().output_schema is LiabilityOutput


@pytest.mark.parametrize(
    "description,expected",
    [
        ("肺炎住院治疗", "covered"),
        ("种植牙两颗", "not_covered"),
        ("自费乙类药3000元治疗", "partial"),
    ],
)
async def test_fallback_matrix(description: str, expected: str) -> None:
    node = make_liability_judge_node(MiniRecorder(), invoker=keyword_only_invoker)
    update = await node(_state(incident_description=description))
    assert update["liability"]["verdict"] == expected
