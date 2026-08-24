"""services/llm/client 单元测试（mock 网络层，不真实调用 API）。

- 验证双模型从配置读取（主链路 / vision 各自的 model 名）
- 验证供应商切换（base_url / api_key / model 均来自配置）
- 验证 invoke / bind_tools 走 OpenAI 兼容协议（mock ChatOpenAI 底层 openai client）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from openai.types.chat import ChatCompletion

import services.llm.client as llm_client
from services.llm.client import get_chat_model, get_vision_model, reset_model_cache


def _mock_raw_response(payload: dict) -> MagicMock:
    """构造 with_raw_response.parse() 的返回对象（其 .parse() 返回 ChatCompletion）。"""
    raw = MagicMock()
    raw.parse.return_value = ChatCompletion.model_validate(payload)
    return raw


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    """每个测试前后清空模型单例，避免配置串扰。

    注入占位 API Key：ChatOpenAI 实例化要求非空凭证，
    本测试组全部 mock 网络层，占位 Key 不会产生真实调用。
    """
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "sk-test-placeholder")
    reset_model_cache()
    yield
    reset_model_cache()


def test_chat_model_uses_configured_main_model() -> None:
    """主链路模型读取 llm_model 配置（deepseek-v4-flash，D007）。"""
    model = get_chat_model()
    assert model.model_name == "deepseek-v4-flash"


def test_vision_model_is_independent_config() -> None:
    """vision 模型独立配置项 llm_vision_model（D008 混合策略）。"""
    model = get_vision_model()
    assert model.model_name == "deepseek-v4-flash-vision-exp"
    # 两个是不同实例，互不影响
    assert get_chat_model() is not get_vision_model()


def test_models_are_cached_singletons() -> None:
    """同参数调用返回同一实例（进程内单例）。"""
    assert get_chat_model() is get_chat_model()
    assert get_vision_model() is get_vision_model()


def test_base_url_points_to_deepseek() -> None:
    """供应商可配置：base_url 来自配置（当前指向 DeepSeek）。"""
    model = get_chat_model()
    assert model.openai_api_base == "https://api.deepseek.com"


def test_provider_switch_via_config(monkeypatch) -> None:
    """模拟切换供应商：改 base_url + model 后新实例生效。"""
    monkeypatch.setattr(llm_client.settings, "llm_base_url", "https://api.other-llm.com/v1")
    monkeypatch.setattr(llm_client.settings, "llm_model", "other-model-x")
    monkeypatch.setattr(llm_client.settings, "llm_api_key", "sk-other")

    model = get_chat_model()
    assert model.model_name == "other-model-x"
    assert model.openai_api_base == "https://api.other-llm.com/v1"


async def test_chat_model_invoke_via_mocked_network() -> None:
    """mock 网络层：invoke 走 OpenAI 兼容协议并返回 AIMessage。"""
    model = get_chat_model()

    fake_payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "测试响应"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with patch.object(
        model.async_client.with_raw_response,
        "create",
        AsyncMock(return_value=_mock_raw_response(fake_payload)),
    ) as mocked:
        result = await model.ainvoke([("user", "你好")])

    assert isinstance(result, AIMessage)
    assert result.content == "测试响应"
    mocked.assert_awaited_once()
    # 请求体校验：模型名与消息结构符合 OpenAI 兼容协议
    _, kwargs = mocked.call_args
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["messages"][0]["role"] == "user"


async def test_chat_model_supports_tool_binding() -> None:
    """bind_tools 生成工具调用能力（多 Agent 核心依赖，OpenAI function calling 协议）。"""
    model = get_chat_model()
    tooled = model.bind_tools(
        [
            {
                "name": "policy_query",
                "description": "根据保单号查询保单详情",
                "parameters": {
                    "type": "object",
                    "properties": {"policy_no": {"type": "string"}},
                    "required": ["policy_no"],
                },
            }
        ]
    )
    # bind 后的 runnable 仍可 ainvoke（mock 网络层验证工具传入请求）
    fake_payload = {
        "id": "chatcmpl-test2",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "policy_query", "arguments": '{"policy_no": "POL-2025-0001"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }
    with patch.object(
        tooled.async_client.with_raw_response,
        "create",
        AsyncMock(return_value=_mock_raw_response(fake_payload)),
    ) as mocked:
        result = await tooled.ainvoke([("user", "查保单 POL-2025-0001")])

    assert result.tool_calls is not None
    assert result.tool_calls[0]["name"] == "policy_query"
    assert result.tool_calls[0]["args"] == {"policy_no": "POL-2025-0001"}
    _, kwargs = mocked.call_args
    assert kwargs["tools"][0]["function"]["name"] == "policy_query"
