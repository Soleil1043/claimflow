"""工具注册中心：注册 / 发现 / 批量导出 OpenAI 工具定义。"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool


class ToolNotFoundError(KeyError):
    """按名称取工具未找到。"""


class ToolRegistry:
    """工具注册中心（进程内单例使用，见 get_default_registry）。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool[Any, Any]] = {}

    def register(self, tool: BaseTool[Any, Any]) -> None:
        """注册工具；重名视为编程错误，直接抛异常（内部代码信任约定）。"""
        if tool.name in self._tools:
            msg = f"工具重复注册: {tool.name}"
            raise ValueError(msg)
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool[Any, Any]:
        """按名称获取工具。"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def list_names(self) -> list[str]:
        """全部已注册工具名。"""
        return sorted(self._tools)

    def to_openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """导出 OpenAI 工具定义；names 为空导出全部（LLM bind_tools 用）。"""
        targets = names if names is not None else self.list_names()
        return [self.get(name).to_openai_tool() for name in targets]


_default_registry: ToolRegistry | None = None


def get_default_registry() -> ToolRegistry:
    """默认全局注册中心（惰性单例）。

    各具体工具模块在 import 时调用 register；应用入口统一 import
    tools 包完成装配（T008 起补充）。
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry
