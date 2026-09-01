"""官方工具基类（T044，D021/D022）：继承 langchain_core.tools.BaseTool。

v2 约定：
- `args_schema`：Pydantic 入参模型（框架自动校验；bind_tools 自动生成 schema）
- `_arun(**kwargs) -> dict`：返回业务 dict；业务失败（保单不存在等）作为正常返回内容，
  含 `success=False` / `error_message` 键（兼容 v1 消费端与 ToolMessage 语义，
  T046 子图化后由 LLM 直接阅读）
- 系统异常向上抛：守卫层（tools/guards.py）负责超时/重试/熔断，兼容壳负责适配
- `to_openai_tool()`：过渡方法——v1 节点仍按 OpenAI 规格构造 bind_tools，T046/T047 删除
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool as LangchainBaseTool


class ClaimflowTool(LangchainBaseTool):
    """项目工具基类：统一提供 v1 兼容的 OpenAI 规格导出（过渡）。"""

    model_config = {"arbitrary_types_allowed": True}

    def to_openai_tool(self) -> dict[str, Any]:
        """生成 OpenAI function calling 工具定义（bind_tools 用，v1 兼容）。"""
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
