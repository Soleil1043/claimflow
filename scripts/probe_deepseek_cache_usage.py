"""诊断：DeepSeek Prompt Caching 非标字段在 langchain-openai 响应链路的存活位置（T162）。

背景：DeepSeek API 在 usage 里返回 prompt_cache_hit_tokens / prompt_cache_miss_tokens
（非 OpenAI 标准字段）。本脚本用 mock 的 ChatCompletion 实测这些字段在
usage_metadata / response_metadata / llm_output 三个位置的存活情况，结论决定
services/observability/llm_metrics.py `_extract_cache_usage` 的提取策略。

结论（2026-10-08 实测，langchain-openai 1.x + openai SDK）：
- usage_metadata：非标字段被丢弃（_create_usage_metadata 只映射标准字段）
- ainvoke 路径的 message.response_metadata["token_usage"]：原始 usage dict 原样存活
  （run manager 把 llm_output 写进 message）——提取函数的第一来源
- 回调 on_llm_end 的 llm_output["token_usage"]：同样存活（备选方案，未采用）

跑法：uv run python scripts/probe_deepseek_cache_usage.py（无需 API Key，全 mock）
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionUsage


def _fake_completion() -> ChatCompletion:
    """构造带 DeepSeek 非标 usage 字段的 ChatCompletion（模拟 API 真实返回形态）。"""
    usage = CompletionUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    # DeepSeek 实际返回的非标字段（OpenAI SDK 的 extra="allow" 保留）
    object.__setattr__(
        usage,
        "__pydantic_extra__",
        {"prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20},
    )
    return ChatCompletion(
        id="cmpl-probe",
        model="deepseek-v4-flash",
        object="chat.completion",
        created=0,
        choices=[
            Choice(
                index=0,
                finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content="ok"),
            )
        ],
        usage=usage,
    )


def _mocked_model() -> Any:
    """ChatOpenAI 假件：openai 异步客户端被 mock，返回构造好的 Completion。"""
    from langchain_openai import ChatOpenAI

    raw = MagicMock()
    raw.parse = MagicMock(return_value=_fake_completion())
    model = ChatOpenAI(
        model="deepseek-v4-flash", api_key="probe", base_url="https://api.deepseek.com"
    )
    model.async_client = MagicMock()
    model.async_client.with_raw_response = MagicMock()
    model.async_client.with_raw_response.create = AsyncMock(return_value=raw)
    return model


async def probe() -> None:
    from langchain_core.messages import HumanMessage

    model = _mocked_model()
    # ainvoke = 生产路径（observed_ainvoke 就走这条）；_agenerate 私有方法不建 run manager，
    # response_metadata 为空——这正是提取必须走 ainvoke 路径的原因
    msg = await model.ainvoke([HumanMessage("hi")])

    usage_metadata: dict = msg.usage_metadata or {}
    response_metadata: dict = msg.response_metadata or {}
    token_usage: dict = response_metadata.get("token_usage") or {}

    print("=== usage_metadata（langchain 标准映射）===")
    print(usage_metadata)
    print("=== response_metadata.token_usage（OpenAI SDK 原始 dict）===")
    print(token_usage)
    print("=== 存活判定 ===")
    print(
        "usage_metadata.input_token_details.cache_read =",
        (usage_metadata.get("input_token_details") or {}).get("cache_read"),
    )
    print(
        "token_usage.prompt_cache_hit_tokens =",
        token_usage.get("prompt_cache_hit_tokens"),
    )
    print(
        "token_usage.prompt_cache_miss_tokens =",
        token_usage.get("prompt_cache_miss_tokens"),
    )
    survived = token_usage.get("prompt_cache_hit_tokens")
    print(
        "\n结论：",
        "非标字段经 response_metadata.token_usage 存活，_extract_cache_usage 第一来源有效"
        if survived
        else "非标字段未存活——langchain-openai 版本可能变了，需重定提取策略",
    )


if __name__ == "__main__":
    asyncio.run(probe())
