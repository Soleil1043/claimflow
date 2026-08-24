"""claim_calculator 工具测试（F05 验收）。

覆盖：
- 标准用例（保额 100 万/免赔 1 万/比例 80%）
- 费用低于免赔额（可赔基数为负 → 0 赔付场景）
- 费用超保额（基数封顶）
- 免赔额 ≥ 保额（无可赔空间，success=False）
- 零免赔重疾险场景
- 金额四舍五入精度
- 入参类型宽松性与非法值校验
"""

from __future__ import annotations

import pytest

from tools.claim.calculator import ClaimCalculatorTool


@pytest.fixture()
def tool() -> ClaimCalculatorTool:
    return ClaimCalculatorTool()


async def test_standard_case(tool: ClaimCalculatorTool) -> None:
    """F05 验收主用例：保额 100 万/免赔 1 万/比例 80%、费用 15,800。

    可赔基数 = min(15800, 1000000) - 10000 = 5800；赔付 = 5800×0.8 = 4640
    """
    result = await tool.execute(
        {
            "medical_expense": 15800,
            "coverage_amount": 1000000,
            "deductible": 10000,
            "payout_ratio": 0.8,
        }
    )
    assert result.success is True
    assert result.data["estimated_payout"] == 4640.0
    detail = result.data["calculation_detail"]
    assert detail["payable_base"] == 5800.0


async def test_expense_exceeds_coverage_capped(tool: ClaimCalculatorTool) -> None:
    """费用超保额：基数封顶为（保额-免赔额）。"""
    result = await tool.execute(
        {
            "medical_expense": 2000000,
            "coverage_amount": 1000000,
            "deductible": 10000,
            "payout_ratio": 0.8,
        }
    )
    assert result.success is True
    # 基数 = 1000000 - 10000 = 990000，赔付 = 792000
    assert result.data["estimated_payout"] == 792000.0


async def test_expense_below_deductible(tool: ClaimCalculatorTool) -> None:
    """费用低于免赔额：可赔基数为 0 → 无法赔付并说明自担原因。"""
    result = await tool.execute(
        {
            "medical_expense": 8000,
            "coverage_amount": 1000000,
            "deductible": 10000,
            "payout_ratio": 0.8,
        }
    )
    assert result.success is False
    assert "未超过免赔额" in (result.error_message or "")
    assert result.data["estimated_payout"] == 0.0


async def test_deductible_exceeds_coverage(tool: ClaimCalculatorTool) -> None:
    """免赔额超保额：无可赔空间，success=False 且说明原因（验收边界用例）。"""
    result = await tool.execute(
        {
            "medical_expense": 50000,
            "coverage_amount": 30000,
            "deductible": 50000,
            "payout_ratio": 0.7,
        }
    )
    assert result.success is False
    assert "无可赔付空间" in (result.error_message or "")
    assert result.data["estimated_payout"] == 0.0


async def test_zero_deductible_full_ratio(tool: ClaimCalculatorTool) -> None:
    """零免赔 + 100% 比例（重疾险场景，POL-2025-0002）：赔付 = min(费用, 保额)。"""
    result = await tool.execute(
        {
            "medical_expense": 500000,
            "coverage_amount": 500000,
            "deductible": 0,
            "payout_ratio": 1.0,
        }
    )
    assert result.success is True
    assert result.data["estimated_payout"] == 500000.0


async def test_rounding_precision(tool: ClaimCalculatorTool) -> None:
    """金额四舍五入到分：1599.995 → 1600.00 级别精度。"""
    result = await tool.execute(
        {
            "medical_expense": 2000.55,
            "coverage_amount": 1000000,
            "deductible": 0,
            "payout_ratio": 0.8,
        }
    )
    assert result.success is True
    # 2000.55 × 0.8 = 1600.44（ROUND_HALF_UP 到分）
    assert result.data["estimated_payout"] == 1600.44


async def test_input_accepts_string_numbers(tool: ClaimCalculatorTool) -> None:
    """入参宽松性：LLM 可能给字符串数字，validator 统一转 Decimal。"""
    result = await tool.execute(
        {
            "medical_expense": "15800",
            "coverage_amount": "1000000",
            "deductible": "10000",
            "payout_ratio": "0.8",
        }
    )
    assert result.success is True
    assert result.data["estimated_payout"] == 4640.0


async def test_input_rejects_invalid_ratio(tool: ClaimCalculatorTool) -> None:
    """非法比例（>1）被 schema 拒绝：success=False 入参校验失败。"""
    result = await tool.execute(
        {
            "medical_expense": 10000,
            "coverage_amount": 1000000,
            "deductible": 0,
            "payout_ratio": 1.5,
        }
    )
    assert result.success is False
    assert "入参校验失败" in (result.error_message or "")


async def test_input_rejects_negative_expense(tool: ClaimCalculatorTool) -> None:
    """负数费用被 schema 拒绝。"""
    result = await tool.execute(
        {
            "medical_expense": -100,
            "coverage_amount": 1000000,
            "deductible": 0,
            "payout_ratio": 0.8,
        }
    )
    assert result.success is False


def test_openai_tool_definition() -> None:
    """工具描述与参数 schema 符合 function calling 格式。"""
    definition = ClaimCalculatorTool().to_openai_tool()
    fn = definition["function"]
    assert fn["name"] == "claim_calculator"
    assert "赔付" in fn["description"]
    props = fn["parameters"]["properties"]
    assert set(props) == {"medical_expense", "coverage_amount", "deductible", "payout_ratio"}
    required = fn["parameters"]["required"]
    assert set(required) == {"medical_expense", "coverage_amount", "deductible", "payout_ratio"}
