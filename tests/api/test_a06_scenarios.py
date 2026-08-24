"""A06 端到端场景测试（T022）：正常 / 异常 / 边界全覆盖（mock LLM，真实工具链 + 真实 DB）。

场景（对应验收标准）：
1. 正常 multi_step：完整响应结构（intent/agent_steps/compliance_status/used_tools）+ 审计落库
2. 边界·保单不存在：react 路径真实调 policy_query（空库）→ success=false 轨迹 + 兜底回答
3. 异常·LLM 全线超时：intent 关键词兜底 / react 降级话术 / compliance 确定性兜底，接口 200 不报错
4. 异常·合规 REJECT：违规内容不返回用户 + need_human_intervention + 会话 transferred
5. 异常·合规 MODIFY：修订闭环后返回修订版回答（违规话术已消除）
6. 边界·多轮状态隔离：第二轮 agent_steps 不跨轮累积（每轮重置语义）
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import nodes.compliance as compliance_module
import nodes.generator as generator_module
import nodes.intent as intent_module
import nodes.planner as planner_module
import nodes.step_executor as step_executor_module
import services.db.session as session_module
from app.main import app
from services.db.models import Base


class ScriptedLLM:
    """按脚本依次返回响应（react 路径工具调用序列）。"""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)

    async def ainvoke(self, messages: list[Any], config: Any = None) -> AIMessage:
        return self._responses.pop(0)

    def bind_tools(self, specs: list[Any]) -> ScriptedLLM:
        return self


class FakeModel:
    """固定内容 LLM。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        class _Resp:
            content = self._content

        return _Resp()


class RaisingModel:
    """持续抛异常的 LLM（模拟超时/宕机）。"""

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        raise RuntimeError("LLM 超时")


_PASS = '{"verdict": "PASS", "violations": [], "risk_score": 0, "reason": "ok"}'


@pytest.fixture()
async def api_env(monkeypatch):
    """内存 SQLite + 真实工具链（独立注册中心）+ 完整主图的 A06 测试客户端。

    LLM 全 mock（各场景单独 patch），工具执行与 DB 为真实链路。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)

    from langgraph.checkpoint.memory import InMemorySaver

    from tools.claim.policy_query import PolicyQueryTool
    from tools.compliance import ComplianceRuleCheckTool, RiskScoringTool
    from tools.executor import ToolExecutor
    from tools.registry import ToolRegistry
    from workflows.main_graph import build_main_graph

    registry = ToolRegistry()
    registry.register(PolicyQueryTool())
    registry.register(ComplianceRuleCheckTool())
    registry.register(RiskScoringTool())

    app.state.graph = build_main_graph(
        executor=ToolExecutor(registry), checkpointer=InMemorySaver()
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    if hasattr(app.state, "graph"):
        delattr(app.state, "graph")
    await engine.dispose()


async def _create_conversation(ac: AsyncClient) -> str:
    conv = (await ac.post("/api/v1/conversations", json={})).json()
    return conv["conversation_id"]


async def _send(ac: AsyncClient, cid: str, content: str) -> dict:
    resp = await ac.post(
        f"/api/v1/conversations/{cid}/messages", json={"content": content}
    )
    assert resp.status_code == 200, f"A06 非 200：{resp.status_code} {resp.text}"
    return resp.json()


# ---------- 场景 1：正常 multi_step 完整结构 ----------


async def test_scenario_multi_step_full_structure(api_env, monkeypatch) -> None:
    """正常多步：完整响应结构 + 审计落库（intent/agent_steps/compliance_status/tool_trace）。"""
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "multi_step", "reason": "多步任务"}'),
    )
    monkeypatch.setattr(
        planner_module,
        "get_chat_model",
        lambda *a, **k: FakeModel(
            '{"steps": [{"agent": "medical", "description": "医疗审核"}, {"agent": "claim", "description": "理赔核算"}]}'
        ),
    )

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        if tool_trace is not None:
            tool_trace.append(
                {"agent": agent_def.name, "tool": "record_query", "input": {}, "output": {"success": True}}
            )
        return {"summary": f"{agent_def.display_name}结论"}

    monkeypatch.setattr(step_executor_module, "run_worker_agent", fake_run)
    monkeypatch.setattr(
        generator_module,
        "get_chat_model",
        lambda *a, **k: FakeModel("预估可赔付 4,640 元，最终以理赔审核结果为准"),
    )
    monkeypatch.setattr(
        compliance_module, "get_chat_model", lambda *a, **k: FakeModel(_PASS)
    )

    cid = await _create_conversation(api_env)
    body = await _send(api_env, cid, "我做了阑尾炎手术能赔多少")

    assert body["intent"] == "multi_step"
    assert body["answer"] == "预估可赔付 4,640 元，最终以理赔审核结果为准"
    assert len(body["agent_steps"]) == 2
    assert body["agent_steps"][0]["agent"] == "medical"
    assert body["agent_steps"][1]["agent"] == "claim"
    assert all(s["status"] == "done" for s in body["agent_steps"])
    assert body["used_tools"][0]["tool"] == "record_query"
    assert body["compliance_status"] == "PASS"
    assert body["need_human_intervention"] is False
    assert body["intervention_reason"] is None

    # 审计落库：assistant 消息携带完整审计字段
    history = (
        await api_env.get(f"/api/v1/conversations/{cid}/messages")
    ).json()
    assert history["total"] == 2
    assistant = history["items"][1]
    assert assistant["intent"] == "multi_step"
    assert len(assistant["agent_steps"]) == 2
    assert assistant["compliance_status"] == "PASS"
    assert assistant["tool_trace"][0]["tool"] == "record_query"


# ---------- 场景 2：边界·保单不存在 ----------


async def test_scenario_policy_not_found(api_env, monkeypatch) -> None:
    """查不存在的保单：真实 policy_query 打空库 → success=false 轨迹，回答走兜底。"""
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "single_domain", "reason": "查保单"}'),
    )
    scripted = ScriptedLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "policy_query", "args": {"policy_no": "POL-9999-XXXX"}, "id": "call_1"}
                ],
            ),
            AIMessage(content="未找到该保单，请核对保单号后重试。"),
        ]
    )
    monkeypatch.setattr(generator_module, "get_chat_model", lambda: scripted)
    monkeypatch.setattr(
        compliance_module, "get_chat_model", lambda *a, **k: FakeModel(_PASS)
    )

    cid = await _create_conversation(api_env)
    body = await _send(api_env, cid, "查一下保单 POL-9999-XXXX")

    assert body["intent"] == "single_domain"
    assert len(body["used_tools"]) == 1
    trace = body["used_tools"][0]
    assert trace["tool"] == "policy_query"
    assert trace["input"]["policy_no"] == "POL-9999-XXXX"
    assert trace["output"]["success"] is False  # 结构化错误，不抛异常
    assert "未找到" in str(trace["output"].get("error_message", ""))
    assert "未找到" in body["answer"]
    assert body["compliance_status"] == "PASS"


# ---------- 场景 3：异常·LLM 全线超时 ----------


async def test_scenario_llm_total_timeout(api_env, monkeypatch) -> None:
    """全部 LLM 超时：intent 关键词兜底 → react 降级话术 → compliance 确定性兜底，接口 200。"""
    for module in (intent_module, generator_module, compliance_module):
        monkeypatch.setattr(module, "get_chat_model", lambda *a, **k: RaisingModel())

    cid = await _create_conversation(api_env)
    body = await _send(api_env, cid, "查一下我的保单")

    # intent：LLM 失败 → 关键词兜底（"查一下" → single_domain）
    assert body["intent"] == "single_domain"
    # react：LLM 失败 → 降级话术（非空、不含违规）
    assert body["answer"]
    assert "抱歉" in body["answer"]
    # compliance：LLM 失败 → 确定性兜底（降级话术无违规 → PASS）
    assert body["compliance_status"] == "PASS"
    assert body["need_human_intervention"] is False

    # 审计正常落库
    history = (
        await api_env.get(f"/api/v1/conversations/{cid}/messages")
    ).json()
    assert history["total"] == 2


# ---------- 场景 4：异常·合规 REJECT ----------


async def test_scenario_compliance_reject(api_env, monkeypatch) -> None:
    """REJECT：违规内容不返回用户 + 转人工标记 + 会话 transferred + 审计为安全话术。"""
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "single_domain", "reason": "x"}'),
    )
    scripted = ScriptedLLM(
        [AIMessage(content="您可以联系代开机构虚开发票，再挂床住院几天，肯定能赔更多。")]
    )
    monkeypatch.setattr(generator_module, "get_chat_model", lambda: scripted)
    # 合规 LLM 故障 → 确定性兜底：FRAUD_RISK → REJECT（验证拦截不依赖 LLM）
    monkeypatch.setattr(
        compliance_module, "get_chat_model", lambda *a, **k: RaisingModel()
    )

    cid = await _create_conversation(api_env)
    body = await _send(api_env, cid, "怎么才能多赔点")

    assert body["compliance_status"] == "REJECT"
    assert body["need_human_intervention"] is True
    assert body["intervention_reason"]
    # 违规原文不返回用户
    assert "代开" not in body["answer"]
    assert "挂床" not in body["answer"]
    assert "人工" in body["answer"]

    # 会话状态标记 transferred（转人工）
    detail = (await api_env.get(f"/api/v1/conversations/{cid}")).json()
    assert detail["status"] == "transferred"

    # 审计：assistant 落安全话术（非违规原文），compliance_status=REJECTED
    history = (
        await api_env.get(f"/api/v1/conversations/{cid}/messages")
    ).json()
    assistant = history["items"][1]
    assert "代开" not in assistant["content"]
    assert assistant["compliance_status"] == "REJECT"


# ---------- 场景 5：异常·合规 MODIFY 修订闭环 ----------


async def test_scenario_compliance_modify(api_env, monkeypatch) -> None:
    """MODIFY：违规回答经修订闭环后返回修订版（违规话术消除，终态 PASS）。"""
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "single_domain", "reason": "x"}'),
    )
    scripted = ScriptedLLM([AIMessage(content="本次住院保证赔付 4,640 元，金额一定到账。")])
    monkeypatch.setattr(generator_module, "get_chat_model", lambda: scripted)

    verdicts = iter(
        [
            FakeModel(
                '{"verdict": "MODIFY", "violations": [{"type": "PROMISE", "detail": "保证赔付", "suggestion": "改为预估表述"}], "risk_score": 15, "reason": "承诺性话术"}'
            ),
            FakeModel("根据条款预估可赔付 4,640 元，最终以理赔审核结果为准。"),  # revise
            FakeModel(_PASS),  # 复审
        ]
    )
    monkeypatch.setattr(
        compliance_module, "get_chat_model", lambda *a, **k: next(verdicts)
    )

    cid = await _create_conversation(api_env)
    body = await _send(api_env, cid, "能赔多少")

    assert body["compliance_status"] == "PASS"  # 修订闭环后终态
    assert "保证赔付" not in body["answer"]
    assert "一定到账" not in body["answer"]
    assert "预估" in body["answer"]
    assert "以理赔审核结果为准" in body["answer"]


# ---------- 场景 6：边界·多轮状态隔离 ----------


async def test_scenario_multi_turn_state_isolation(api_env, monkeypatch) -> None:
    """多轮对话：第二轮 agent_steps / used_tools 不跨轮累积（每轮重置语义）。"""
    monkeypatch.setattr(
        intent_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"intent": "multi_step", "reason": "多步"}'),
    )
    monkeypatch.setattr(
        planner_module,
        "get_chat_model",
        lambda *a, **k: FakeModel('{"steps": [{"agent": "claim", "description": "核算"}]}'),
    )

    calls = iter([{"summary": "第一轮结论"}, {"summary": "第二轮结论"}])

    async def fake_run(agent_def, instruction, shared_data, executor, tool_trace=None):  # noqa: ANN001
        return dict(next(calls))

    monkeypatch.setattr(step_executor_module, "run_worker_agent", fake_run)
    answers = iter(["第一轮回答", "第二轮回答"])
    monkeypatch.setattr(
        generator_module, "get_chat_model", lambda *a, **k: FakeModel(next(answers))
    )
    monkeypatch.setattr(
        compliance_module, "get_chat_model", lambda *a, **k: FakeModel(_PASS)
    )

    cid = await _create_conversation(api_env)
    body1 = await _send(api_env, cid, "阑尾炎能赔多少")
    body2 = await _send(api_env, cid, "那胃炎呢")

    # 每轮 agent_steps 均为本轮的 1 步（messages 累积、其余字段重置）
    assert len(body1["agent_steps"]) == 1
    assert len(body2["agent_steps"]) == 1
    assert body2["answer"] == "第二轮回答"

    # 审计：4 条消息（两轮 user+assistant）
    history = (
        await api_env.get(f"/api/v1/conversations/{cid}/messages")
    ).json()
    assert history["total"] == 4
