"""Worker 子图执行器测试（T046）：create_agent 装配 / 结构化输出 / 轨迹派生 / 降级。

不依赖真实 LLM：以假模型（bind_tools 透传 + 消息队列脚本）预构建子图，
预置进 runner 的子图缓存后走 run_worker_agent 全链路（含轨迹派生与降级）。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, PrivateAttr

import agents.runner as runner_module
from agents import CLAIM_AGENT, MEDICAL_AGENT
from agents.runner import (
    _derive_tool_trace,
    reset_worker_cache,
    run_worker_agent,
)
from schemas.agent_outputs import ClaimAgentOutput
from tools.base import ClaimflowTool


class EchoIn(BaseModel):
    text: str = "hi"


class EchoTool(ClaimflowTool):
    name: str = "echo"
    description: str = "测试用回显工具"
    args_schema: type[EchoIn] = EchoIn
    _calls: int = PrivateAttr(default=0)

    def _run(self, *args: object, **kwargs: object) -> dict:
        raise NotImplementedError("仅支持异步调用")

    async def _arun(self, *, text: str = "hi") -> dict:
        self._calls += 1
        return {"success": True, "echo": text, "calls": self._calls}


class ProbeTool(EchoTool):
    """以 CLAIM_AGENT 真实工具名暴露的回显桩（进轨迹白名单，免 DB）。"""

    name: str = "policy_query"


class FakeChatModel(GenericFakeChatModel):
    """消息队列假模型：bind_tools 透传（create_agent 装配要求）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeChatModel:
        return self


def _seed_worker(agent_def, script: list[AIMessage], tool: EchoTool | None = None):
    """用假模型预构建子图并预置缓存（绕过真实 get_chat_model）。"""
    echo = tool or EchoTool()
    model = FakeChatModel(messages=iter(script))
    worker = create_agent(
        model=model,
        tools=[echo],
        system_prompt=agent_def.system_prompt,
        response_format=agent_def.output_schema,
        name=agent_def.name,
    )
    runner_module._worker_cache[agent_def.name] = worker
    return echo


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_worker_cache()
    yield
    reset_worker_cache()


# ---------- 结构化终局输出 ----------


async def test_structured_response_returned() -> None:
    """工具循环后终局调用结构化输出工具 → 返回 schema 实例 dump。"""
    script = [
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]),
        AIMessage(
            content="结论",
            tool_calls=[
                {
                    "name": "ClaimAgentOutput",  # ToolStrategy：以 schema 类名命名的隐藏工具
                    "args": {"summary": "预估赔付 4640 元", "warnings": ["以审核为准"]},
                    "id": "c2",
                }
            ],
        ),
        AIMessage(content="ack"),
    ]
    _seed_worker(CLAIM_AGENT, script)

    result = await run_worker_agent(CLAIM_AGENT, "算赔付", {})
    assert result["summary"] == "预估赔付 4640 元"
    assert result["warnings"] == ["以审核为准"]


# ---------- 轨迹派生 ----------


async def test_tool_trace_derived_from_messages() -> None:
    """tool_trace 由 messages 派生：真实工具入列，结构化输出工具排除。"""
    script = [
        AIMessage(
            content="", tool_calls=[{"name": "policy_query", "args": {"text": "hi"}, "id": "c1"}]
        ),
        AIMessage(
            content="结论",
            tool_calls=[
                {"name": "ClaimAgentOutput", "args": {"summary": "s"}, "id": "c2"},
            ],
        ),
        AIMessage(content="ack"),
    ]
    trace: list[dict[str, Any]] = []
    _seed_worker(CLAIM_AGENT, script, tool=ProbeTool())

    await run_worker_agent(CLAIM_AGENT, "算赔付", {}, tool_trace=trace)
    assert len(trace) == 1  # policy_query 入列；ClaimAgentOutput（非业务工具）排除
    entry = trace[0]
    assert entry["agent"] == "claim"
    assert entry["tool"] == "policy_query"
    assert entry["input"] == {"text": "hi"}
    assert entry["output"]["echo"] == "hi"  # ToolMessage JSON 串已解析


def test_derive_tool_trace_unparsable_output() -> None:
    """ToolMessage 内容非 JSON：降级 raw 字段（不抛错）。"""
    from langchain_core.messages import ToolMessage

    msgs = [
        AIMessage(content="", tool_calls=[{"name": "echo", "args": {}, "id": "c1"}]),
        ToolMessage(content="不是 JSON 的输出", tool_call_id="c1", name="echo"),
    ]
    trace = _derive_tool_trace("claim", msgs, {"echo"})
    assert trace[0]["output"] == {"raw": "不是 JSON 的输出"}


# ---------- 降级 ----------


async def test_fallback_summary_when_no_structured_output() -> None:
    """模型未调用结构化输出工具：最后一条有内容 AIMessage 降级为 summary。"""
    script = [
        AIMessage(content="我觉得可以直接文字回答"),
    ]
    _seed_worker(MEDICAL_AGENT, script)

    result = await run_worker_agent(MEDICAL_AGENT, "核对诊断", {})
    assert result == {"summary": "我觉得可以直接文字回答"}


async def test_subgraph_exception_propagates() -> None:
    """子图执行异常向上抛（step_executor 捕获记 failed）。"""

    class _RaisingModel:
        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM 故障")

    runner_module._worker_cache[CLAIM_AGENT.name] = create_agent(
        model=_RaisingModel(),
        tools=[EchoTool()],
        system_prompt=CLAIM_AGENT.system_prompt,
        response_format=ClaimAgentOutput,
        name=CLAIM_AGENT.name,
    )
    with pytest.raises(RuntimeError, match="LLM 故障"):
        await run_worker_agent(CLAIM_AGENT, "算赔付", {})


# ---------- shared_data 注入 ----------


async def test_shared_data_injected_into_task_message() -> None:
    """shared_data 上下文进入输入消息（Worker 可引用前序结论）。"""
    captured: dict[str, Any] = {}

    class _CapturingFake(FakeChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

    class _Cap(_CapturingFake):
        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            captured["content"] = str(messages[-1].content)
            raise RuntimeError("短路")

    runner_module._worker_cache[MEDICAL_AGENT.name] = create_agent(
        model=_Cap(messages=iter([AIMessage(content="")])),
        tools=[EchoTool()],
        system_prompt=MEDICAL_AGENT.system_prompt,
        response_format=MEDICAL_AGENT.output_schema,
        name=MEDICAL_AGENT.name,
    )
    with pytest.raises(RuntimeError):
        await run_worker_agent(MEDICAL_AGENT, "核对诊断", {"claim": {"summary": "赔付 4640 元"}})
    assert "核对诊断" in captured["content"]
    assert "赔付 4640 元" in captured["content"]
