"""金额理算规格公式（T097，评审候选 5）：运行时与金样本生成器的唯一实现。

规格：approved = min(max(claimed − self_pay − deductible, 0) × ratio, coverage)，
量化到分。全链路 Decimal，严禁 float 参与计算（F08 硬门前提）。
"""

from __future__ import annotations

from decimal import Decimal

_CENT = Decimal("0.01")


def approved_amount(
    claimed: Decimal,
    self_pay: Decimal,
    deductible: Decimal,
    ratio: Decimal,
    coverage: Decimal,
) -> Decimal:
    """核定金额：先扣自费/乙类自付与免赔额，乘赔付比例，保额封顶，不出现负数。"""
    raw = (claimed - self_pay - deductible) * ratio
    return min(max(raw, Decimal("0")), coverage).quantize(_CENT)
