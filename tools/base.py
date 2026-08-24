"""BaseTool 工具基类（AGENTS.md 6.1 工具层约定）。

每个工具继承 BaseTool 并实现：
- name / description（给 LLM 看，说明何时使用该工具）
- input_schema / output_schema（Pydantic 类型，用于入参校验与 OpenAI 工具 schema 生成）
- _run(input_data)：业务逻辑，返回 ToolOutput 子类实例

execute() 由基类统一提供：入参 schema 校验 + 结构化日志，不被子类覆盖。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from schemas.tools import ToolInput, ToolOutput

log = get_logger(__name__)


class BaseTool[I: ToolInput, O: ToolOutput](ABC):
    """工具抽象基类。

    泛型参数：
        I: 输入 schema（ToolInput 子类）
        O: 输出 schema（ToolOutput 子类）
    """

    name: str
    description: str
    input_schema: type[I]
    output_schema: type[O]

    @abstractmethod
    async def _run(self, input_data: I) -> O:
        """业务逻辑：子类实现，输入已通过 schema 校验。"""

    async def execute(self, input_data: dict[str, Any] | BaseModel) -> ToolOutput:
        """统一执行入口：校验入参 → 执行 → 记录结构化日志。

        供 ToolExecutor 调用；LLM 侧的入参是 dict，在此校验并转换。
        """
        if isinstance(input_data, BaseModel):
            validated = input_data
        else:
            try:
                validated = self.input_schema.model_validate(input_data)
            except ValidationError as exc:
                # 入参不合法属于调用方（LLM）错误，不重试，直接返回失败
                log.warning("tool_input_invalid", tool=self.name, errors=exc.errors()[:3])
                return ToolOutput(success=False, error_message=f"入参校验失败: {exc.errors()[0]['msg']}")

        started = time.perf_counter()
        result = await self._run(validated)
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "tool_executed",
            tool=self.name,
            success=result.success,
            duration_ms=duration_ms,
        )
        return result

    def to_openai_tool(self) -> dict[str, Any]:
        """生成 OpenAI function calling 的工具定义（bind_tools 用）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema.model_json_schema(),
            },
        }
