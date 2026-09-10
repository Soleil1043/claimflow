"""Worker 子图容错中间件单测（T115，D052 自愈层）。

ToolErrorMiddleware 语义：工具系统异常 → error ToolMessage（LLM 可见）→ 模型换路自愈；
反复失败由 ModelCallLimit 硬截断收口 → 未结构化降级 {"summary": ...}，全程不抛错。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

import services.worker_agent as wa
from services.worker_agent import AgentDefinition, invoke_worker, reset_worker_cache


class BoomInput(BaseModel):
    x: int


class BoomTool(BaseTool):
    """总是抛系统异常的工具（测试用）。"""

    name: str = "boom"
    description: str = "总是失败的工具（测试用）"
    args_schema: type[BaseModel] = BoomInput

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("仅支持异步")

    async def _arun(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom down")


class WorkerOut(BaseModel):
    conclusion: str


class ScriptedModel(GenericFakeChatModel):
    """脚本化模型：忽略 create_agent 的工具绑定（按预排消息应答）。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:  # noqa: ARG002
        return self


def _agent_def() -> AgentDefinition:
    return AgentDefinition(
        name="t115_test_worker",
        display_name="测试Worker",
        system_prompt="测试用 Worker",
        tool_names=["boom"],
        output_schema=WorkerOut,
        description="T115 容错中间件测试",
    )


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


@pytest.fixture()
def worker_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """补一个脚本化模型 + 失败工具，子图缓存用后复位。"""

    def make_model(messages: list[AIMessage]) -> None:
        monkeypatch.setattr(
            wa,
            "get_chat_model",
            lambda temperature=0.1: ScriptedModel(messages=iter(messages)),
        )
        monkeypatch.setattr(wa, "get_default_tool_map", lambda: {"boom": BoomTool()})
        reset_worker_cache()

    yield make_model
    reset_worker_cache()


async def test_tool_error_self_heals(worker_env) -> None:
    """工具系统异常 → error ToolMessage → 模型换路产出结构化结论（不炸子图）。"""
    worker_env(
        [
            _tool_call("boom", {"x": 1}, "call_1"),
            _tool_call("WorkerOut", {"conclusion": "基于已有信息收口"}, "call_2"),
        ]
    )
    result, messages = await invoke_worker(_agent_def(), "任务：测试")

    assert result == {"conclusion": "基于已有信息收口"}
    error_msgs = [m for m in messages if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(error_msgs) == 1
    # 自愈层只暴露异常类型，不泄露原始异常消息（官方建议口径）
    assert "RuntimeError" in str(error_msgs[0].content)
    assert "boom down" not in str(error_msgs[0].content)
    assert "boom" in str(error_msgs[0].content)


async def test_tool_error_exhaustion_degrades(worker_env) -> None:
    """模型反复调用失败工具：ModelCallLimit 截断收口 → 降级 summary，全程不抛错。"""
    worker_env([_tool_call("boom", {"x": i}, f"call_{i}") for i in range(12)])
    result, messages = await invoke_worker(_agent_def(), "任务：测试")

    # 未产出结构化结论 → 最后一条 AIMessage 原文降级（不向上抛）
    assert set(result) == {"summary"}
    assert isinstance(result["summary"], str)
    error_msgs = [
        m for m in messages if isinstance(m, ToolMessage) and m.status == "error"
    ]
    assert error_msgs, "失败工具调用应收到 error ToolMessage"
