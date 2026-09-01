"""Agent 定义基类（AGENTS.md 6.2）。

Agent = system prompt + 可用工具集 + 结构化输出 schema 的静态描述。
T046 起 Worker 执行体为 langchain.agents.create_agent 官方子图
（agents/runner.py 装配），本类仅承载配置，不含执行逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel


@dataclass(frozen=True)
class AgentDefinition:
    """Agent 静态定义（create_agent 三要素：system_prompt / tools / response_format）。

    Attributes:
        name: Agent 标识（图节点 / 计划步骤引用）
        display_name: 展示名（日志 / 演示）
        system_prompt: 系统提示词（来自 services/llm/prompts.py 常量）
        tool_names: 可用工具名列表（执行时从工具工厂的默认工具图解析）
        output_schema: 结构化终局输出 schema（response_format，Pydantic 模型）
        description: Agent 职责一句话描述（给 Orchestrator 规划用）
    """

    name: str
    display_name: str
    system_prompt: str
    tool_names: list[str]
    output_schema: type[BaseModel]
    description: str
    # Agent 专属运行参数（预留）
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve_tool_objects(self, tool_map: dict[str, BaseTool]) -> list[BaseTool]:
        """从工具图解析本 Agent 可用的工具对象（跳过未装配的，v1 行为一致）。"""
        return [tool_map[name] for name in self.tool_names if name in tool_map]
