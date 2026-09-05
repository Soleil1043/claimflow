"""schemas/lines.py 险种 pack 注册表测试（T096，候选 2）。"""

from __future__ import annotations

from schemas.lines import (
    LINE_PACKS,
    all_doc_types,
    get_line_pack,
    online_lines,
    pack_for_product_type,
)


def test_medical_pack_is_online_with_full_knowledge() -> None:
    """首批医疗险 pack：上线 + 材料/条款/兜底规则齐备。"""
    pack = get_line_pack("medical")
    assert pack is not None and pack.online
    assert [dt for dt, _ in pack.required_docs] == ["invoice", "diagnosis", "cost_list"]
    assert pack.policy_terms["waiting_period_days"] == 30
    assert ("整形", "整形美容") in pack.exclusion_keywords
    assert pack.self_pay_pattern is not None


def test_second_phase_lines_declared_but_offline() -> None:
    """auto/property/accident 已声明未上线——受理期转人工（D039）。"""
    assert online_lines() == frozenset({"medical"})
    for line in ("auto", "property", "accident"):
        pack = get_line_pack(line)
        assert pack is not None and not pack.online


def test_product_type_mapping_covers_all_packs() -> None:
    """保单产品类型 → pack 归属（intake 分类口径）；未识别返回 None。"""
    assert pack_for_product_type("医疗险") is get_line_pack("medical")
    assert pack_for_product_type("车险") is get_line_pack("auto")
    assert pack_for_product_type("航意险") is None


def test_unknown_line_returns_none() -> None:
    """unknown / 空值无 pack——节点侧兜底规则退化为仅保单事实判定。"""
    assert get_line_pack("unknown") is None
    assert get_line_pack(None) is None
    assert get_line_pack("") is None


def test_doc_type_whitelist_is_union_of_packs() -> None:
    """API 上传白名单 = 全部 pack doc_types 并集。"""
    assert all_doc_types() == frozenset({"invoice", "diagnosis", "cost_list", "medical_record"})
    assert set(all_doc_types()) >= set(get_line_pack("medical").doc_types)
    assert len(LINE_PACKS) == 4
