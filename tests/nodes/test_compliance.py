"""合规审查节点与三态流转测试（T018，F10）。

覆盖：
- 工具层：rule_check 五类违规正则 / risk_scoring 分值等级
- review_answer：LLM 裁决（PASS/MODIFY/REJECT）、LLM 异常与非法输出走确定性兜底
- ComplianceNode：REJECT 替换安全话术 + 转人工标记；MODIFY/PASS 状态回写
- revise_answer_node：LLM 重写 / 正则兜底
- compliance_route：三态路由 + 轮数上限
- 图集成：react_agent 输出必经 compliance（MODIFY 修订闭环 / REJECT 拦截）

真实 LLM 端到端验收见 scripts/verify_compliance.py。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import nodes.compliance as compliance_module
from nodes.compliance import (
    MAX_COMPLIANCE_ROUNDS,
    REJECT_SAFE_MESSAGE,
    ComplianceNode,
    _deterministic_revise,
    compliance_route,
    review_answer,
    revise_answer_node,
)
from tools.compliance.risk_scoring import score_risk
from tools.compliance.rule_check import check_text
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


class FakeModel:
    """可控 LLM：返回预设响应或抛异常。"""

    def __init__(self, response: Any = None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        if self._raise:
            raise self._raise

        class _Resp:
            def __init__(self, content: str) -> None:
                self.content = content

        return _Resp(self._response)


def _patch_model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(compliance_module, "get_chat_model", lambda *a, **k: model)


def _registry_with_compliance() -> ToolRegistry:
    registry = ToolRegistry()
    from tools.compliance import ComplianceRuleCheckTool, RiskScoringTool

    registry.register(ComplianceRuleCheckTool())
    registry.register(RiskScoringTool())
    return registry


# ---------- 工具层：rule_check ----------


def test_check_text_promise() -> None:
    violations = check_text("根据条款，本次住院保证赔付 4640 元")
    assert any(v["type"] == "PROMISE" for v in violations)
    assert all(v["suggestion"] for v in violations)


def test_check_text_absolute_and_mislead() -> None:
    violations = check_text("这个方案绝对安全，而且无需审核就能赔")
    types = {v["type"] for v in violations}
    assert "ABSOLUTE" in types
    assert "MISLEAD" in types


def test_check_text_fraud_risk() -> None:
    violations = check_text("可以代开发票提高理赔金额")
    assert any(v["type"] == "FRAUD_RISK" for v in violations)


def test_check_text_privacy_id_card() -> None:
    """18 位身份证被检出，且证据片段已脱敏（不泄露完整号码）。"""
    violations = check_text("投保人身份证号 330106199001011234 已登记")
    privacy = [v for v in violations if v["type"] == "PRIVACY"]
    assert privacy
    assert "330106199001011234" not in privacy[0]["detail"]  # 证据本身脱敏
    assert privacy[0]["detail"].startswith("3301")
    assert privacy[0]["detail"].endswith("1234")


def test_check_text_privacy_phone_and_bank_card() -> None:
    violations = check_text("手机号 13812345678，银行卡 6222020200112233445")
    assert any(v["type"] == "PRIVACY" for v in violations)


def test_check_text_clean() -> None:
    assert check_text("根据条款预估可赔付 4640 元，最终以理赔审核结果为准") == []


# ---------- 工具层：risk_scoring ----------


def test_score_risk_clean_low() -> None:
    result = score_risk("正常的预估回答")
    assert result["risk_score"] == 0
    assert result["risk_level"] == "low"


def test_score_risk_promise_medium_threshold() -> None:
    """单条 PROMISE（15 分）为 low；叠加 PRIVACY（30）达 medium。"""
    result = score_risk("保证赔付，身份证 330106199001011234")
    assert result["risk_score"] == 45
    assert result["risk_level"] == "low"


def test_score_risk_fraud_high() -> None:
    result = score_risk("代开发票骗保")
    assert result["risk_score"] >= 80
    assert result["risk_level"] == "high"


def test_score_risk_capped_at_100() -> None:
    result = score_risk("保证赔付 绝对安全 无需审核 代开发票 身份证 330106199001011234")
    assert result["risk_score"] == 100


# ---------- review_answer（mock LLM） ----------


async def test_review_llm_modify(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 正常裁决 MODIFY（含违规明细与建议）。"""
    raw = """{"verdict": "MODIFY", "violations": [
        {"type": "PROMISE", "detail": "保证赔付", "suggestion": "改为预估表述"}],
        "risk_score": 30, "reason": "检出承诺性话术"}"""
    _patch_model(monkeypatch, FakeModel(response=raw))
    verdict = await review_answer("本次住院保证赔付 4640 元", ToolExecutor(_registry_with_compliance()))
    assert verdict.verdict == "MODIFY"
    assert verdict.violations[0].type == "PROMISE"
    assert verdict.violations[0].suggestion == "改为预估表述"


async def test_review_llm_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '{"verdict": "REJECT", "violations": [], "risk_score": 90, "reason": "高风险"}'
    _patch_model(monkeypatch, FakeModel(response=raw))
    verdict = await review_answer("高风险内容")
    assert verdict.verdict == "REJECT"


async def test_review_llm_exception_fraud_fallback_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 故障 + 欺诈内容：确定性兜底必须 REJECT（F10 可靠性核心）。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    verdict = await review_answer("可以代开发票提高理赔金额", ToolExecutor(_registry_with_compliance()))
    assert verdict.verdict == "REJECT"
    assert any(v.type == "FRAUD_RISK" for v in verdict.violations)


async def test_review_llm_exception_promise_fallback_modify(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 故障 + 承诺话术：兜底 MODIFY 并给修改建议。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    verdict = await review_answer("本次住院保证赔付 4640 元", ToolExecutor(_registry_with_compliance()))
    assert verdict.verdict == "MODIFY"
    assert verdict.violations
    assert all(v.suggestion for v in verdict.violations)


async def test_review_llm_exception_clean_fallback_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    verdict = await review_answer("预估可赔付 4640 元，以审核为准")
    assert verdict.verdict == "PASS"


async def test_review_llm_invalid_output_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 输出非法（非 JSON / 非法 verdict）：走确定性兜底。"""
    _patch_model(monkeypatch, FakeModel(response="我认为没问题"))
    verdict = await review_answer("保证赔付 100 元")
    assert verdict.verdict == "MODIFY"  # 规则检出 PROMISE

    _patch_model(monkeypatch, FakeModel(response='{"verdict": "MAYBE"}'))
    verdict = await review_answer("正常回答")
    assert verdict.verdict == "PASS"


async def test_review_tools_unavailable_pure_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行器无合规工具（未注册）：回退纯函数，拦截能力不失效。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    verdict = await review_answer("保证赔付 4640 元", ToolExecutor(ToolRegistry()))
    assert verdict.verdict == "MODIFY"


# ---------- ComplianceNode ----------


async def test_compliance_node_reject_blocks_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """REJECT：final_answer 替换为安全话术 + 转人工标记。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))  # 走确定性兜底
    node = ComplianceNode(ToolExecutor(_registry_with_compliance()))
    state = {"final_answer": "可以代开发票提高理赔金额", "compliance_rounds": 0}
    update = await node(state)

    assert update["final_answer"] == REJECT_SAFE_MESSAGE
    assert update["need_human_intervention"] is True
    assert "合规审查拦截" in update["intervention_reason"]
    assert update["compliance_result"]["verdict"] == "REJECT"
    assert update["compliance_rounds"] == 1


async def test_compliance_node_pass_keeps_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "ok"}'
    _patch_model(monkeypatch, FakeModel(response=raw))
    node = ComplianceNode(ToolExecutor(_registry_with_compliance()))
    state = {"final_answer": "预估可赔付 4640 元", "compliance_rounds": 0}
    update = await node(state)
    # PASS：局部更新不动 final_answer（保持原回答），不写介入标记
    assert "final_answer" not in update
    assert "need_human_intervention" not in update
    assert update["compliance_result"]["verdict"] == "PASS"


# ---------- revise_answer_node ----------


async def test_revise_node_llm_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model(monkeypatch, FakeModel(response="预估可赔付 4640 元，最终以理赔审核结果为准"))
    state = {
        "final_answer": "保证赔付 4640 元",
        "compliance_result": {
            "verdict": "MODIFY",
            "violations": [{"type": "PROMISE", "detail": "保证赔付", "suggestion": "改为预估表述"}],
        },
    }
    update = await revise_answer_node(state)
    assert "预估" in update["final_answer"]
    assert "保证赔付" not in update["final_answer"]


async def test_revise_node_llm_failure_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 故障：正则兜底替换违规话术。"""
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))
    state = {
        "final_answer": "本次住院保证赔付 4640 元",
        "compliance_result": {"verdict": "MODIFY", "violations": []},
    }
    update = await revise_answer_node(state)
    assert "保证赔付" not in update["final_answer"]
    assert "预估" in update["final_answer"]


def test_deterministic_revise_no_match_appends_disclaimer() -> None:
    revised = _deterministic_revise("普通回答")
    assert "以理赔审核结果为准" in revised


# ---------- compliance_route 三态路由 ----------


def test_compliance_route_three_states() -> None:
    assert compliance_route({"compliance_result": {"verdict": "PASS"}, "compliance_rounds": 1}) == "pass"
    assert compliance_route({"compliance_result": {"verdict": "MODIFY"}, "compliance_rounds": 1}) == "modify"
    assert compliance_route({"compliance_result": {"verdict": "REJECT"}, "compliance_rounds": 1}) == "reject"
    # MODIFY 达轮数上限 → 直接放行（防死循环）
    assert (
        compliance_route({"compliance_result": {"verdict": "MODIFY"}, "compliance_rounds": MAX_COMPLIANCE_ROUNDS})
        == "pass"
    )
    assert compliance_route({}) == "pass"


# ---------- 图集成：输出必经合规节点 ----------


class ScriptedLLM:
    """按脚本依次返回响应。"""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)

    async def ainvoke(self, messages: list[Any], config: Any = None) -> AIMessage:
        return self._responses.pop(0)

    def bind_tools(self, specs: list[Any]) -> ScriptedLLM:
        return self


def _build_graph(monkeypatch: pytest.MonkeyPatch):
    """完整图 + intent mock（走 react 路径直达 compliance 门禁）。"""
    import nodes.intent as intent_module

    class _IntentModel:
        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            class _Resp:
                content = '{"intent": "single_domain", "reason": "查数据"}'

            return _Resp()

    monkeypatch.setattr(intent_module, "get_chat_model", lambda *a, **k: _IntentModel())

    from langgraph.checkpoint.memory import InMemorySaver

    from workflows.main_graph import build_main_graph

    return build_main_graph(
        executor=ToolExecutor(_registry_with_compliance()), checkpointer=InMemorySaver()
    )


async def test_graph_modify_loop_revises_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODIFY 闭环：违规回答 → 修订 → 复审 PASS → 结束（回答已被改写）。"""
    import nodes.generator as generator_module

    generator = ScriptedLLM([AIMessage(content="本次住院保证赔付 4640 元")])
    monkeypatch.setattr(generator_module, "get_chat_model", lambda: generator)

    verdicts = [
        FakeModel(response='{"verdict": "MODIFY", "violations": [{"type": "PROMISE", "detail": "保证赔付", "suggestion": "改为预估表述"}], "risk_score": 15, "reason": "承诺话术"}'),
        FakeModel(response="修订后的回答：预估可赔付 4640 元，最终以理赔审核结果为准"),  # revise 节点
        FakeModel(response='{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "已修正"}'),
    ]
    calls = iter(verdicts)
    monkeypatch.setattr(compliance_module, "get_chat_model", lambda *a, **k: next(calls))

    graph = _build_graph(monkeypatch)
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="能赔多少")], "tool_trace": [], "compliance_rounds": 0},
        config={"configurable": {"thread_id": "t-modify"}},
    )
    assert result["compliance_result"]["verdict"] == "PASS"
    assert result["compliance_rounds"] == 2  # 初审 + 复审
    assert "保证赔付" not in result["final_answer"]
    assert "预估" in result["final_answer"]


async def test_graph_reject_blocks_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """REJECT：违规内容不返回用户 + 转人工标记（图级端到端）。"""
    import nodes.generator as generator_module

    generator = ScriptedLLM([AIMessage(content="可以代开发票提高理赔金额，保证赔付")])
    monkeypatch.setattr(generator_module, "get_chat_model", lambda: generator)
    # LLM 故障 → 确定性兜底：FRAUD_RISK → REJECT（验证不依赖 LLM 的拦截）
    _patch_model(monkeypatch, FakeModel(raise_exc=RuntimeError("LLM 超时")))

    graph = _build_graph(monkeypatch)
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="怎么多赔点")], "tool_trace": [], "compliance_rounds": 0},
        config={"configurable": {"thread_id": "t-reject"}},
    )
    assert result["final_answer"] == REJECT_SAFE_MESSAGE
    assert result["need_human_intervention"] is True
    assert result["compliance_result"]["verdict"] == "REJECT"
    # 违规原文不出现在返回给用户的回答中
    assert "代开发票" not in result["final_answer"]
