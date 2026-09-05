"""schemas/contract.py 规格契约一致性测试（T097，候选 5）。

契约是运行时 / 金样本生成器 / 评测门的唯一数值来源——
任何一侧手抄数值与本模块脱钩即在本测试暴露。
"""

from __future__ import annotations

from decimal import Decimal

from schemas import contract
from schemas.lines import MEDICAL_PACK
from services.amounts import approved_amount


def test_config_defaults_follow_contract() -> None:
    """config 字段默认值引用契约（模型字段默认，非实例值——env 覆盖属合法配置）。"""
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["auto_approve_limit"].default == contract.AUTO_APPROVE_LIMIT
    assert fields["routing_call_budget"].default == contract.ROUTING_CALL_BUDGET


def test_medical_pack_waiting_period_follows_contract() -> None:
    """医疗险 pack 条款等待期引用契约。"""
    assert MEDICAL_PACK.policy_terms["waiting_period_days"] == contract.WAITING_PERIOD_DAYS


def test_amounts_formula_clamp_and_cap() -> None:
    """规格公式：先扣后乘、负数归零、保额封顶、量化到分。"""
    assert approved_amount(
        Decimal("17500"), Decimal("0"), Decimal("2500"), Decimal("0.8"), Decimal("100000")
    ) == Decimal("12000.00")
    # 低于免赔额 → 0（不出现负数）
    assert approved_amount(
        Decimal("1000"), Decimal("0"), Decimal("2500"), Decimal("0.8"), Decimal("100000")
    ) == Decimal("0.00")
    # 超保额 → 封顶
    assert approved_amount(
        Decimal("999999"), Decimal("0"), Decimal("0"), Decimal("0.9"), Decimal("50000")
    ) == Decimal("50000.00")
    # 含自费扣减
    assert approved_amount(
        Decimal("17500"), Decimal("2500"), Decimal("2500"), Decimal("0.8"), Decimal("100000")
    ) == Decimal("10000.00")
