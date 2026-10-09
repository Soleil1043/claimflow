"""敏感信息脱敏工具（T019，F11）。

对文本中的敏感信息脱敏：
- 18 位身份证号：保留前 4 后 4 位 → 3301**********1234
- 银行卡号（16-19 位）：保留前 4 后 4 位 → 6222**********3445
- 手机号（11 位）：保留前 3 后 4 位 → 138****5678

正则与脱敏函数单一来源在 app.core.pii（T175/D081：日志零 PII 处理器与
合规链路共用同一套模式，避免两处正则漂移）；本模块 re-export 供既有调用方
（decision_doc / human_gate 等）不变。

纯函数 mask_sensitive / find_sensitive 供合规链路直接调用；
工具经 tools/factory.py 装配后供 Compliance Agent（T015 定义）使用。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.core.pii import find_sensitive, mask_sensitive
from schemas.tools import ToolInput
from tools.base import ClaimflowTool

__all__ = ["find_sensitive", "mask_sensitive", "SensitiveFilterTool"]


class SensitiveFilterInput(ToolInput):
    """脱敏入参。"""

    text: str = Field(description="待脱敏的文本", min_length=1)


class SensitiveFilterTool(ClaimflowTool):
    # 注：name/description 必须带类型注解——pydantic 要求子类覆盖父类字段时显式标注
    name: str = "sensitive_filter"
    description: str = (
        "对文本中的敏感信息脱敏：18 位身份证号保留前 4 后 4 位（如 3301**********1234）、"
        "银行卡号保留前 4 后 4 位、手机号保留前 3 后 4 位。"
        "回答中包含用户证件号/卡号/手机号需要脱敏时使用。"
    )
    args_schema: type[SensitiveFilterInput] = SensitiveFilterInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步壳（langchain 1.x 要求实现 _run）：本项目全链路 async，同步路径不可用。"""
        raise NotImplementedError(f"{self.name} 仅支持异步调用（ainvoke）")

    async def _arun(self, *, text: str) -> dict[str, Any]:
        findings = find_sensitive(text)
        masked = mask_sensitive(text)
        return {
            "success": True,
            "masked_text": masked,
            "findings": findings,
            "masked_count": len(findings),
        }
