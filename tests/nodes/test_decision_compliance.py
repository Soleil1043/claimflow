"""决定书生成 + 合规门节点测试（T085）：三态闭环 / 修订版本化 / writer 三态。"""

from __future__ import annotations

import json
from decimal import Decimal

import nodes.compliance_gate as cg_module
import nodes.decision_generate as dg_module
from nodes.compliance_gate import (
    compliance_route,
    make_compliance_gate_node,
    make_revise_decision_node,
)
from nodes.decision_generate import make_decision_generate_node
from services.decision_doc import render_decision_document


class MiniRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.decisions: list[dict] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append({"case_id": case_id, "kind": kind, "stage": stage, "payload": payload})

    async def update_case(self, case_id, **kwargs) -> None:
        pass

    async def save_decision(self, case_id, **kwargs) -> None:
        self.decisions.append({"case_id": case_id, **kwargs})


_LIABILITY = {
    "verdict": "covered",
    "reason": "急性阑尾炎手术属住院医疗责任",
    "clause_references": ["第三条 保险责任"],
}
_CALC = {"approved_amount": "4640.00",
         "calculation_basis": "(15800-10000免赔)*0.8=4640.00", "deductions": []}
_MATERIAL = {"completeness": "complete", "missing": [], "confidence": 1.0}


def _state(**overrides) -> dict:
    base = {
        "case_id": "CASE-2026-0001",
        "case_type": "medical",
        "liability": _LIABILITY,
        "calc": _CALC,
        "material": _MATERIAL,
    }
    base.update(overrides)
    return base


# ---------- decision_generate：writer 三态 ----------


async def test_fallback_writer_deterministic() -> None:
    """默认 writer=None → 纯代码叙述（零 LLM，确定性，T096 统一 None 语义）。"""
    node = make_decision_generate_node(MiniRecorder(), writer=None)
    update = await node(_state())
    doc = update["decision"]
    assert doc["conclusion"] == "approved"
    assert Decimal(str(doc["approved_amount"])) == Decimal("4640.00")
    assert "核定金额：4640.00 元" in doc["body"]


async def test_llm_writer_skill_assembled_and_fail_open(monkeypatch) -> None:
    """真实 writer：skill 装配进提示词；抛错 → fail-open 回退规则叙述。"""
    captured: list[str] = []

    async def writer(state):
        system = dg_module.build_system_prompt(
            dg_module.DECISION_NARRATIVE_PROMPT,
            "decision_writer",
            state.get("case_type") or "_shared",
            facts=dg_module._narrative_facts(state),
        )
        captured.append(system)
        raise RuntimeError("LLM 超时")

    node = make_decision_generate_node(MiniRecorder(), writer=writer)
    update = await node(_state())
    # fail-open：叙述回退规则版，决定书仍产出
    assert "经审核，" in update["decision"]["body"]
    # skill 装配断言（提示词在异常前已构建）
    assert captured and ("置信度" in captured[0] or "公文语体" in captured[0])


def test_writer_factory_disabled(monkeypatch) -> None:
    """decision_writer_llm_enabled=False → 工厂返回 None。"""
    monkeypatch.setattr(dg_module.settings, "decision_writer_llm_enabled", False)
    assert dg_module.make_decision_writer() is None


# ---------- compliance_gate：三态 ----------


async def test_gate_pass_on_consistent_doc() -> None:
    rec = MiniRecorder()
    doc = render_decision_document(
        case_id="C1", case_type="medical", liability=_LIABILITY,
        calc=_CALC, narrative=None,
    )
    node = make_compliance_gate_node(rec)
    update = await node(_state(decision=doc))
    assert update["compliance"]["verdict"] == "PASS"
    assert update["compliance"]["amount_consistent"] is True


async def test_gate_amount_tamper_modify() -> None:
    """金额注入：正文金额被篡改 → MODIFY（非 PASS），金额断言命中。"""
    doc = render_decision_document(
        case_id="C1", case_type="medical", liability=_LIABILITY,
        calc=_CALC, narrative=None,
    )
    tampered = {**doc, "approved_amount": "9999.00",
                "body": doc["body"].replace("4640.00", "9999.00")}
    node = make_compliance_gate_node(MiniRecorder())
    update = await node(_state(decision=tampered))
    assert update["compliance"]["verdict"] == "MODIFY"
    assert update["compliance"]["amount_consistent"] is False


async def test_gate_red_line_reject() -> None:
    """红线话术注入 → REJECT（图上 reject 条件边 → human_gate 转人工）。"""
    doc = render_decision_document(
        case_id="C1", case_type="medical", liability=_LIABILITY,
        calc=_CALC, narrative=None,
    )
    tampered = {**doc, "body": doc["body"] + "\n本公司保证赔付。"}
    node = make_compliance_gate_node(MiniRecorder())
    update = await node(_state(decision=tampered))
    assert update["compliance"]["verdict"] == "REJECT"


def test_gate_route_mapping() -> None:
    """三态 → 条件边路由（pass/modify/reject→human_gate）。"""
    assert compliance_route({"compliance": {"verdict": "PASS"}}) == "pass"
    assert compliance_route({"compliance": {"verdict": "MODIFY"}}) == "modify"
    assert compliance_route({"compliance": {"verdict": "REJECT"}}) == "reject"


# ---------- 修订闭环 ----------


async def test_revise_rerenders_and_bumps_version(monkeypatch) -> None:
    """MODIFY → revise 代码重渲染（规则叙述，version+1 落库）→ 复审 PASS（闭环）。"""
    monkeypatch.setattr(cg_module.settings, "compliance_max_rounds", 2)
    rec = MiniRecorder()
    doc = render_decision_document(
        case_id="C1", case_type="medical", liability=_LIABILITY,
        calc=_CALC, narrative="本公司保证赔付。",  # 首版含红线（模拟叙述失控）
    )
    state = _state(decision=doc, compliance={"verdict": "MODIFY", "round": 1})

    # 修订：丢弃 LLM 叙述，规则版重渲染
    revise = make_revise_decision_node(rec)
    update = await revise(state)
    assert update["decision"]["version"] == 2
    assert "保证赔付" not in update["decision"]["body"]
    assert rec.decisions and "保证赔付" not in rec.decisions[0]["body"]

    # 复审：重渲染后金额一致、无红线 → PASS
    gate = make_compliance_gate_node(rec)
    state2 = _state(decision=update["decision"])
    review = await gate(state2)
    assert review["compliance"]["verdict"] == "PASS"


async def test_gate_max_rounds_reject(monkeypatch) -> None:
    """修订超轮数上限 → REJECT 转人工（防死循环）。"""
    monkeypatch.setattr(cg_module.settings, "compliance_max_rounds", 2)
    doc = render_decision_document(
        case_id="C1", case_type="medical", liability=_LIABILITY,
        calc=_CALC, narrative=None,
    )
    tampered = {**doc, "approved_amount": "9999.00",
                "body": doc["body"].replace("4640.00", "9999.00")}
    node = make_compliance_gate_node(MiniRecorder())
    update = await node(
        _state(decision=tampered, compliance={"verdict": "MODIFY", "round": 2})
    )
    assert update["compliance"]["verdict"] == "REJECT"
    assert any(v["type"] == "max_rounds" for v in update["compliance"]["violations"])


def test_review_json_serializable() -> None:
    doc = render_decision_document(
        case_id="C1", case_type="medical", liability=_LIABILITY,
        calc=_CALC, narrative=None,
    )
    node = make_compliance_gate_node(MiniRecorder())

    async def run():
        return await node(_state(decision=doc))

    import asyncio

    review = asyncio.run(run())["compliance"]
    json.dumps(review, ensure_ascii=False)
