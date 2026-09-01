"""工具注册中心（T044 过渡形态）。

v1 的「import 即全局注册」副作用已删除：默认注册中心从工具工厂
（tools/factory.py）惰性填充，装配显式化。本类保留为名称→工具的轻量容器，
供 v1 消费端（ToolExecutor 兼容壳）与测试渐进迁移；T046/T047 消费端
子图化改持工具列表后，本模块整体删除。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool


class ToolNotFoundError(KeyError):
    """按名称取工具未找到。"""


class ToolRegistry:
    """工具容器（进程内默认实例见 get_default_registry）。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具；重名视为编程错误，直接抛异常（内部代码信任约定）。"""
        if tool.name in self._tools:
            msg = f"工具重复注册: {tool.name}"
            raise ValueError(msg)
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """按名称获取工具。"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def list_names(self) -> list[str]:
        """全部已注册工具名。"""
        return sorted(self._tools)


_default_registry: ToolRegistry | None = None


def get_default_registry() -> ToolRegistry:
    """默认注册中心：惰性从工具工厂装配填充（v1 调用方零改动）。"""
    global _default_registry
    if _default_registry is None:
        from tools.factory import get_default_tool_map

        _default_registry = ToolRegistry()
        for tool in get_default_tool_map().values():
            _default_registry.register(tool)
    return _default_registry
