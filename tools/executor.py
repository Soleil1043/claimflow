"""ToolExecutor 兼容壳（T044）。

v1 集中执行层（超时 / 重试 / 熔断 / 缓存 / 指标）已下沉至工具层：
tools/factory.py 装配（官方 .with_retry + GuardedTool 守卫），随工具对象走。
本类保留 v1 公共接口（execute / breaker_state / registry 属性）供现有节点、
脚本与测试渐进迁移，T046/T047 消费端替换后删除。

职责（仅剩适配）：
- 查工具 → `ainvoke`（dict 出入）→ 适配 v1 ToolOutput 信封
- 入参校验失败（ValidationError）→ success=False 返回（不重试，v1 语义）
- 未守卫的裸工具异常 → 包装 ToolExecutionError（守卫工具已在守卫层包装）
- per-call fallback：捕获 ToolExecutionError 后返回降级结果（仅测试使用）
- tracing span 仍在此处包装（守卫层迁移随 T046 子图化进行）
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.exceptions import ToolExecutionError
from app.core.logging import get_logger
from schemas.tools import ToolOutput
from services.observability import metrics
from tools.registry import ToolRegistry

log = get_logger(__name__)

# ToolOutput 信封拆包键（工具 _arun 返回 dict 的约定键）
_ENVELOPE_KEYS = ("success", "error_message")


class ToolExecutor:
    """v1 兼容执行入口：Agent / 节点不直接调工具，一律经此执行（过渡）。"""

    def __init__(
        self,
        registry: ToolRegistry,
        # v1 构造参数（超时/重试/熔断配置）已随守卫下沉工厂，此处兼容签名忽略
        **_legacy_kwargs: Any,
    ) -> None:
        self.registry = registry

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any] | BaseModel,
        *,
        timeout: float | None = None,  # noqa: ARG002 兼容签名；超时在守卫层配置
        fallback: ToolOutput | None = None,
    ) -> ToolOutput:
        """执行工具（公共入口，tracing span 包装）。"""
        from services.observability.tracing import ATTR_TOOL_NAME, traced_span

        with traced_span(f"tool.{tool_name}", **{ATTR_TOOL_NAME: tool_name}):
            try:
                return await self._execute(tool_name, input_data, fallback=fallback)
            except ToolExecutionError:
                if fallback is not None:
                    metrics.record_tool_call(tool_name, "fallback", 0.0)
                    return fallback
                raise

    async def _execute(
        self,
        tool_name: str,
        input_data: dict[str, Any] | BaseModel,
        *,
        fallback: ToolOutput | None,
    ) -> ToolOutput:
        """查工具 → ainvoke → dict 适配 ToolOutput。"""
        tool = self.registry.get(tool_name)
        args = input_data if isinstance(input_data, dict) else input_data.model_dump()

        try:
            result = await tool.ainvoke(args)
        except ValidationError as exc:
            # 入参不合法属于调用方（LLM）错误，不重试，直接返回失败（v1 语义）
            log.warning("tool_input_invalid", tool=tool_name, errors=exc.errors()[:3])
            return ToolOutput(
                success=False, error_message=f"入参校验失败: {exc.errors()[0]['msg']}"
            )
        except ToolExecutionError:
            raise  # 守卫工具已在守卫层计数/包装
        except Exception as exc:  # noqa: BLE001 未守卫的裸工具（测试装配）
            raise ToolExecutionError(tool_name, str(exc)[:300]) from exc

        return ToolOutput(
            success=bool(result.get("success", True)),
            error_message=result.get("error_message"),
            data={k: v for k, v in result.items() if k not in _ENVELOPE_KEYS},
        )

    def breaker_state(self, tool_name: str) -> str:
        """查询工具熔断状态（可观测性 / 测试用）。"""
        from tools.guards import GuardedTool

        tool = self.registry.get(tool_name)
        if isinstance(tool, GuardedTool):
            return str(tool.breaker_state)
        return "closed"  # 未守卫的裸工具无熔断
