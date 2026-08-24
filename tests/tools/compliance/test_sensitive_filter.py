"""敏感信息脱敏工具测试（T019，F11）。

验收标准：
- 18 位身份证号输出 3301**********1234 格式
- 银行卡号/手机号脱敏
- 正则模式有单元测试覆盖（身份证/银行卡/手机号/边界/混合/幂等）
"""

from __future__ import annotations

import pytest

from tools.compliance.sensitive_filter import (
    SensitiveFilterTool,
    find_sensitive,
    mask_sensitive,
)
from tools.registry import ToolRegistry

# ---------- 身份证（18 位，验收格式 3301**********1234） ----------


def test_mask_id_card_standard_format() -> None:
    """F11 主验收：18 位身份证 → 3301**********1234。"""
    assert mask_sensitive("身份证号 330106199001011234 已登记") == (
        "身份证号 3301**********1234 已登记"
    )


def test_mask_id_card_with_x_suffix() -> None:
    """末位 X 的身份证同样脱敏。"""
    masked = mask_sensitive("证件 11010119900307861X")
    assert masked == "证件 1101**********861X"
    assert "11010119900307861X" not in masked


def test_mask_id_card_not_matched_with_digits_around() -> None:
    """前后紧贴数字的 18 位串不命中（数字边界）。"""
    assert mask_sensitive("123306011990010112345") == "123306011990010112345"


def test_mask_id_card_15_digits_not_matched() -> None:
    """15 位老身份证不在脱敏范围（正则限定 18 位）。"""
    assert mask_sensitive("330106900101123") == "330106900101123"


# ---------- 银行卡 ----------


def test_mask_bank_card_19_digits() -> None:
    masked = mask_sensitive("收款卡 6222020200112233445")
    assert masked == "收款卡 6222***********3445"  # 19 位 → 中间 11 个星号
    assert "6222020200112233445" not in masked


def test_mask_bank_card_16_digits() -> None:
    masked = mask_sensitive("卡号 6222600123456789")
    assert masked == "卡号 6222********6789"


def test_mask_bank_card_too_long_not_matched() -> None:
    """超 19 位的连续数字（如订单号）不命中。"""
    text = "订单 12345678901234567890"
    assert mask_sensitive(text) == text


# ---------- 手机号 ----------


def test_mask_phone_standard_format() -> None:
    masked = mask_sensitive("联系 13812345678 确认")
    assert masked == "联系 138****5678 确认"
    assert "13812345678" not in masked


def test_mask_phone_all_prefixes() -> None:
    """1[3-9] 号段全覆盖；12/10 号段不命中。"""
    for prefix in ("13", "15", "18", "19"):
        assert "****" in mask_sensitive(f"{prefix}123456789")  # 11 位
    assert mask_sensitive("12345678901") == "12345678901"  # 12 开头不匹配


def test_mask_phone_short_number_not_matched() -> None:
    """固话短号（<11 位）不脱敏。"""
    assert mask_sensitive("拨打 057112345") == "拨打 057112345"


# ---------- 混合与行为 ----------


def test_mask_mixed_all_types() -> None:
    """身份证/银行卡/手机号混合文本一次全部脱敏。"""
    text = "张三 330106199001011234，手机 13812345678，卡号 6222020200112233445"
    masked = mask_sensitive(text)
    assert masked == "张三 3301**********1234，手机 138****5678，卡号 6222***********3445"


def test_mask_id_card_no_bank_card_duplication() -> None:
    """18 位身份证不被银行卡正则二次替换（去重）。"""
    masked = mask_sensitive("330106199001011234")
    assert masked == "3301**********1234"  # 仍为前4后4，未被破坏


def test_mask_clean_text_unchanged() -> None:
    text = "根据条款预估可赔付 4,640 元，最终以理赔审核结果为准"
    assert mask_sensitive(text) == text


def test_mask_idempotent() -> None:
    """脱敏幂等：已脱敏文本再跑一次不变。"""
    once = mask_sensitive("身份证 330106199001011234")
    assert mask_sensitive(once) == once


# ---------- find_sensitive ----------


def test_find_sensitive_reports_details() -> None:
    findings = find_sensitive("330106199001011234 收款 6222020200112233445")
    types = [f["type"] for f in findings]
    assert types == ["id_card", "bank_card"]
    assert findings[0]["masked"] == "3301**********1234"
    assert findings[1]["masked"] == "6222***********3445"
    assert all(f["value"] for f in findings)


def test_find_sensitive_empty() -> None:
    assert find_sensitive("普通文本") == []


# ---------- BaseTool 封装 ----------


async def test_tool_execute() -> None:
    tool = SensitiveFilterTool()
    result = await tool.execute({"text": "联系 13812345678"})
    assert result.success is True
    assert result.data["masked_text"] == "联系 138****5678"
    assert result.data["masked_count"] == 1
    assert result.data["findings"][0]["type"] == "phone"


async def test_tool_registered() -> None:
    """import tools.compliance 后 sensitive_filter 可被发现。"""
    import tools.compliance  # noqa: F401
    from tools.registry import get_default_registry

    registry = get_default_registry()
    assert "sensitive_filter" in registry.list_names()


def test_tool_schema_export() -> None:
    spec = SensitiveFilterTool().to_openai_tool()
    assert spec["function"]["name"] == "sensitive_filter"
    assert "text" in spec["function"]["parameters"]["properties"]


async def test_tool_rejects_empty_input() -> None:
    tool = SensitiveFilterTool()
    result = await tool.execute({"text": ""})
    assert result.success is False


def test_registry_registration_isolated() -> None:
    """独立注册中心也可注册使用（测试友好性）。"""
    registry = ToolRegistry()
    registry.register(SensitiveFilterTool())
    assert registry.get("sensitive_filter") is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("330106199001011234", "3301**********1234"),
        ("6222020200112233445", "6222***********3445"),
        ("13812345678", "138****5678"),
    ],
    ids=["id_card", "bank_card", "phone"],
)
def test_mask_single_value(raw: str, expected: str) -> None:
    assert mask_sensitive(raw) == expected
