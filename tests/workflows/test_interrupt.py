"""LangGraph interrupt 恢复机制测试（T037）。

端到端覆盖（mock LLM，真实图 + 真实 checkpointer）：
- 触发：compliance REJECT → human_review 调 interrupt 挂起（result 含 __interrupt__，
  final_answer 已是安全话术，need_human_intervention=True）
- 恢复：Command(resume=坐席结论) → human_review 重跑，结论经合规复审
  （PASS → 结论返回用户；REJECT → 保守安全话术；空结论 → 安全话术）
- 持久化：共享 checkpointer 重建图实例（模拟服务重启）后 resume 仍可用
- 防御：非 dict / 缺 resolution_note 的 resume 值不抛错
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import nodes.compliance as compliance_module
import nodes.generator as generator_module
import nodes.intent as intent_module
from nodes.compliance import REJECT_SAFE_MESSAGE
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry
from workflows.main_graph import build_main_graph

_RESET_INPUT = {
    "intent": None,
    "task_plan": [],
    "current_step": 0,
    "shared_data": {},
    "agent_steps": [],
    "tool_trace": [],
    "compliance_result": None,
    "compliance_rounds": 0,
    "final_answer": "",
    "need_human_intervention": False,
    "intervention_reason": None,
}


class FakeModel:
    """可控 LLM：固定响应（AIMessage，含空 tool_calls 供 react 条件边判断）。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
        return AIMessage(content=self._content)


class ScriptedLLM:
    """按脚本依次返回（react 路径无工具循环直答）。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def ainvoke(self, messages: Any, **kwargs: Any) -> AIMessage:
        return AIMessage(content=self._responses.pop(0))

    def bind_tools(self, specs: list[Any]) -> ScriptedLLM:
        return self


def _reject_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """react 路径直答 + compliance 确定性 REJECT（LLM 故障兜底：FRAUD_RISK → REJECT）。"""
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "single_domain", "reason": "测试"}'),
    )
    monkeypatch.setattr(
        generator_module,
        "get_chat_model",
        lambda: FakeModel("您可以联系代开机构虚开发票，再挂床住院几天，肯定能赔更多。"),
    )

    class RaisingModel:
        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("合规 LLM 故障，走确定性兜底")

    monkeypatch.setattr(compliance_module, "get_chat_model", lambda *a, **k: RaisingModel())


def _make_graph(checkpointer: InMemorySaver) -> Any:
    return build_main_graph(executor=ToolExecutor(ToolRegistry()), checkpointer=checkpointer)


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def _trigger_reject(graph: Any, thread_id: str) -> dict[str, Any]:
    """发一条消息触发 REJECT → interrupt 挂起，返回 invoke 结果。"""
    return await graph.ainvoke(
        {
            "messages": [HumanMessage(content="怎么才能多赔点")],
            "conversation_id": thread_id,
            **_RESET_INPUT,
        },
        config=_config(thread_id),
    )


# ---------- 触发：REJECT → interrupt 挂起 ----------


async def test_reject_triggers_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """REJECT：图在 human_review 挂起——__interrupt__ 存在、安全话术已就位、介入标记置位。"""
    _reject_patch(monkeypatch)
    graph = _make_graph(InMemorySaver())

    result = await _trigger_reject(graph, "t-interrupt-1")

    assert result.get("__interrupt__"), "REJECT 应产生 interrupt 挂起"
    assert result.get("final_answer") == REJECT_SAFE_MESSAGE  # 安全话术不返回违规内容
    assert result.get("need_human_intervention") is True
    assert result.get("intervention_reason")
    interrupt_payload = result["__interrupt__"][0].value
    assert interrupt_payload["compliance_result"]["verdict"] == "REJECT"  # 挂起载荷含裁决快照


# ---------- 恢复：坐席结论经合规复审 ----------


async def test_resume_with_note_passes_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复：坐席结论复审 PASS → 结论作为 final_answer 返回用户。"""
    _reject_patch(monkeypatch)
    saver = InMemorySaver()
    graph = _make_graph(saver)
    thread_id = "t-resume-pass"
    await _trigger_reject(graph, thread_id)

    # 恢复阶段：复审 LLM 返回 PASS
    monkeypatch.setattr(
        compliance_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            '{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "坐席结论合规"}'
        ),
    )
    resumed = await graph.ainvoke(
        Command(
            resume={
                "resolution_note": "经人工核实：按条款预估赔付 4,640 元，三个工作日内到账。",
                "resolved_by": "agent-01",
            }
        ),
        config=_config(thread_id),
    )
    assert resumed.get("final_answer") == "经人工核实：按条款预估赔付 4,640 元，三个工作日内到账。"
    assert resumed.get("need_human_intervention") is False  # 介入闭环
    assert resumed.get("compliance_result", {}).get("verdict") == "PASS"
    assert not resumed.get("__interrupt__")  # 挂起已消费


async def test_resume_with_violating_note_kept_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复：坐席结论本身违规（复审 REJECT）→ 保守话术，结论不返回用户。"""
    _reject_patch(monkeypatch)
    graph = _make_graph(InMemorySaver())
    thread_id = "t-resume-reject"
    await _trigger_reject(graph, thread_id)

    monkeypatch.setattr(
        compliance_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            '{"verdict": "REJECT", "violations": [{"type": "PROMISE", "detail": "保证赔付"}], "risk_score": 20, "reason": "承诺话术"}'
        ),
    )
    resumed = await graph.ainvoke(
        Command(
            resume={"resolution_note": "保证赔付一百万，肯定到账。", "resolved_by": "agent-02"}
        ),
        config=_config(thread_id),
    )
    assert "保证赔付" not in resumed.get("final_answer", "")
    assert "合规复核" in resumed.get("final_answer", "")


async def test_resume_with_empty_note_keeps_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复：空结论 → 安全话术（不抛错）。"""
    _reject_patch(monkeypatch)
    graph = _make_graph(InMemorySaver())
    thread_id = "t-resume-empty"
    await _trigger_reject(graph, thread_id)

    resumed = await graph.ainvoke(
        Command(resume={"resolution_note": "", "resolved_by": "agent-01"}),
        config=_config(thread_id),
    )
    assert resumed.get("final_answer") == REJECT_SAFE_MESSAGE


async def test_resume_with_invalid_payload_keeps_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复：resume 值非 dict（异常输入）→ 安全话术，不抛错。"""
    _reject_patch(monkeypatch)
    graph = _make_graph(InMemorySaver())
    thread_id = "t-resume-invalid"
    await _trigger_reject(graph, thread_id)

    resumed = await graph.ainvoke(Command(resume="随便一个字符串"), config=_config(thread_id))
    assert resumed.get("final_answer") == REJECT_SAFE_MESSAGE


# ---------- 持久化：跨"服务重启"恢复 ----------


async def test_interrupt_survives_graph_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """挂起状态随 checkpoint 持久化：重建图实例（模拟重启）后 resume 仍可用。"""
    _reject_patch(monkeypatch)
    saver = InMemorySaver()  # 共享 checkpointer = 持久化层
    thread_id = "t-restart"
    await _trigger_reject(_make_graph(saver), thread_id)

    # "服务重启"：同一 checkpointer 重建图（新节点实例）
    monkeypatch.setattr(
        compliance_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            '{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "ok"}'
        ),
    )
    graph2 = _make_graph(saver)
    resumed = await graph2.ainvoke(
        Command(resume={"resolution_note": "重启后恢复的坐席结论。", "resolved_by": "agent-03"}),
        config=_config(thread_id),
    )
    assert resumed.get("final_answer") == "重启后恢复的坐席结论。"


async def test_resume_without_interrupt_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """无挂起的 thread：resolve 侧靠 aget_state(next) 预检跳过（本用例验证快照判据）。"""
    _reject_patch(monkeypatch)
    graph = _make_graph(InMemorySaver())
    # 正常 PASS 轮：无 interrupt
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "chitchat", "reason": "测试"}'),
    )
    monkeypatch.setattr(
        compliance_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            '{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "ok"}'
        ),
    )
    await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")], "conversation_id": "t-noop", **_RESET_INPUT},
        config=_config("t-noop"),
    )
    snapshot = await graph.aget_state(_config("t-noop"))
    assert not snapshot.next  # 无挂起 → resolve 预检跳过恢复
