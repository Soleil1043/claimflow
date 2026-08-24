"""LLM 客户端封装（DeepSeek，OpenAI 兼容接口）。

混合模型策略（decisions.md D007/D008）：
- 主链路模型（llm_model）：意图识别 / 任务规划 / 工具调用 / 回答生成
- 视觉模型（llm_vision_model）：图片 OCR 专职，失败降级 Mock（T020 实现降级逻辑）

供应商 / 模型全部通过配置切换（base_url + api_key + model），
底层用 langchain-openai 的 ChatOpenAI 走 OpenAI 兼容协议。
"""

from __future__ import annotations

from functools import cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# 默认请求参数：对话类任务低温度保证稳定；超时交给 httpx 配置
_DEFAULT_TEMPERATURE = 0.1
_DEFAULT_TIMEOUT = 60


@cache
def get_chat_model(temperature: float = _DEFAULT_TEMPERATURE) -> BaseChatModel:
    """主链路 ChatModel（deepseek-v4-flash），进程内单例。

    用途：意图识别、任务规划、工具调用（bind_tools）、回答生成。
    """
    model = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=temperature,
        timeout=_DEFAULT_TIMEOUT,
        max_retries=2,  # langchain 内建重试（网络层瞬时故障）
    )
    log.info("chat_model_initialized", model=settings.llm_model, base_url=settings.llm_base_url)
    return model


@cache
def get_vision_model(temperature: float = _DEFAULT_TEMPERATURE) -> BaseChatModel:
    """视觉 ChatModel（deepseek-v4-flash-vision-exp），图片 OCR 专职。

    独立配置项 llm_vision_model，与主链路互不影响；
    调用方（tools/medical/ocr_extract.py，T020）负责失败降级 Mock。
    """
    model = ChatOpenAI(
        model=settings.llm_vision_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=temperature,
        timeout=_DEFAULT_TIMEOUT,
        max_retries=1,  # OCR 有 Mock 兜底，减少重试等待
    )
    log.info("vision_model_initialized", model=settings.llm_vision_model)
    return model


def reset_model_cache() -> None:
    """清空模型单例缓存（测试用：切换配置后重建）。"""
    get_chat_model.cache_clear()
    get_vision_model.cache_clear()
