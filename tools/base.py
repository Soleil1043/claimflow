"""官方工具基类（T044，D021/D022）：继承 langchain_core.tools.BaseTool。

约定：
- `args_schema`：Pydantic 入参模型（框架自动校验；bind_tools 自动生成 schema）
- `_arun(**kwargs) -> dict`：返回业务 dict；业务失败（保单不存在等）作为正常返回内容，
  含 `success=False` / `error_message` 键，随 ToolMessage 由 LLM 直接阅读
- 系统异常向上抛：守卫层（tools/guards.py）负责超时/重试/熔断
- `to_openai_tool()`：OpenAI function calling 规格导出（测试断言 schema 用；
  生产路径由 bind_tools 自动生成）
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool as LangchainBaseTool


class ClaimflowTool(LangchainBaseTool):
    """项目工具基类：在官方 BaseTool 之上统一 OpenAI function calling 规格导出。"""

    model_config = {"arbitrary_types_allowed": True}

    def to_openai_tool(self) -> dict[str, Any]:
        """生成 OpenAI function calling 工具定义（测试断言 schema 用）。"""
        schema = self.args_schema
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": (
                    schema.model_json_schema() if isinstance(schema, type) else schema
                ),
            },
        }
