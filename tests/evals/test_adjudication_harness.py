"""evals/adjudication_harness.py error_result 测试（T200/I16：错误行口径）。"""

from __future__ import annotations

from evals.adjudication_harness import error_result
from tests.evals.test_adjudication_dataset import _case


def test_error_result_carries_routing_calls() -> None:
    """I16：错误行必须带 routing_calls 键（预算门不再以默认 0 逃逸）。"""
    row = error_result(_case(), "boom", routing_calls=7)
    assert row["routing_calls"] == 7
    # 缺省兼容：未提供时为 0（键仍存在）
    assert error_result(_case(), "boom")["routing_calls"] == 0


def test_error_result_marks_checks_false() -> None:
    """错误行各判 false、matched=False、错误信息入列（进硬门分母即拉低正确率）。"""
    row = error_result(_case(route="auto"), "timeout")
    assert row["expected_route"] == "auto"
    assert row["matched"] is False
    assert row["checks"]["amount"] is False
    assert row["checks"]["route"] is False
    assert row["error"] == "timeout"
