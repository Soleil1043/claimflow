"""Agent 定义基类（AGENTS.md 6.2）。

Agent = system prompt + 可用工具集 + 结构化输出 schema 的静态描述，
不包含执行逻辑（执行由 LangGraph 节点 / ToolExecutor 驱动）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class AgentDefinition:
    """Agent 静态定义。

    Attributes:
        name: Agent 标识（图节点 / 计划步骤引用）
        display_name: 展示名（日志 / 演示）
        system_prompt: 系统提示词（来自 services/llm/prompts.py 常量）
        tool_names: 可用工具名列表（执行时从注册中心解析）
        output_schema: 结构化输出 schema（Pydantic 模型，LLM 输出校验）
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

    def resolve_tools(self, registry: Any) -> list[Any]:
        """从注册中心解析本 Agent 可用的 OpenAI 工具定义（过滤未注册的）。"""
        from tools.registry import ToolNotFoundError

        specs = []
        for name in self.tool_names:
            try:
                specs.append(registry.get(name).to_openai_tool())
            except ToolNotFoundError:
                # 工具尚未实现（跨任务依赖）：跳过，不阻断 Agent 定义
                continue
        return specs
