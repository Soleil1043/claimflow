"""理赔计算器工具（F05）。

按保额、免赔额、赔付比例计算预估赔付金额。
计算公式（绝对免赔额标准算法）：
    可赔基数 = max(0, min(医疗费用, 保额) - 免赔额)
    预估赔付 = 可赔基数 × 赔付比例
（费用超出保额部分自担；免赔额内自担）

设计说明：
- 纯计算工具（无 DB / 网络依赖），入参由调用方（LLM）从 policy_query 结果提取
- Decimal 全程计算保证金额精度，出口转 float 便于 JSON 序列化
- 业务规则违反（如费用为负、免赔超保额）返回 success=False 说明原因，
  由 Agent 向用户解释，不抛异常（T007 失败语义）
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import Field, field_validator

from schemas.tools import ToolInput, ToolOutput
from tools.base import BaseTool


class ClaimCalculatorInput(ToolInput):
    """理赔计算入参。"""

    medical_expense: Decimal = Field(description="本次医疗费用总额（元）", gt=0)
    coverage_amount: Decimal = Field(description="保额（元）", gt=0)
    deductible: Decimal = Field(description="免赔额（元）", ge=0)
    payout_ratio: Decimal = Field(description="赔付比例（0-1 之间，如 0.8 表示 80%）", gt=0, le=1)

    @field_validator("medical_expense", "coverage_amount", "deductible", "payout_ratio")
    @classmethod
    def _to_decimal(cls, v: Decimal | float | int | str) -> Decimal:
        """宽松接受 float/int/str，统一转 Decimal 计算精度。"""
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class ClaimCalculatorOutput(ToolOutput):
    """理赔计算输出：data 含预估赔付金额与计算明细。"""


class ClaimCalculatorTool(BaseTool[ClaimCalculatorInput, ClaimCalculatorOutput]):
    name = "claim_calculator"
    description = (
        "根据医疗费用、保额、免赔额、赔付比例计算预估赔付金额。"
        "在查询到保单详情后，用户询问'能赔多少'、'赔付金额'时使用。"
        "计算规则：可赔基数 = max(0, min(医疗费用, 保额) - 免赔额)，预估赔付 = 可赔基数 × 赔付比例。"
    )
    input_schema = ClaimCalculatorInput
    output_schema = ClaimCalculatorOutput

    async def _run(self, input_data: ClaimCalculatorInput) -> ClaimCalculatorOutput:
        expense: Decimal = input_data.medical_expense
        coverage: Decimal = input_data.coverage_amount
        deductible: Decimal = input_data.deductible
        ratio: Decimal = input_data.payout_ratio

        # 标准绝对免赔算法：费用先封顶到保额，再扣除免赔额，下限 0
        payable_base = min(expense, coverage) - deductible
        if payable_base <= 0:
            if deductible >= coverage:
                reason = f"免赔额（{deductible} 元）不低于保额（{coverage} 元），无可赔付空间"
            else:
                reason = f"医疗费用（{expense} 元）未超过免赔额（{deductible} 元），费用需自行承担"
            return ClaimCalculatorOutput(
                success=False,
                error_message=reason,
                data={"estimated_payout": 0.0},
            )

        payout = (payable_base * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return ClaimCalculatorOutput(
            success=True,
            data={
                "estimated_payout": float(payout),
                # 计算明细：供 Agent 向用户解释金额构成
                "calculation_detail": {
                    "medical_expense": float(expense),
                    "coverage_amount": float(coverage),
                    "deductible": float(deductible),
                    "payable_base": float(payable_base),  # 可赔基数
                    "payout_ratio": float(ratio),
                },
            },
        )
