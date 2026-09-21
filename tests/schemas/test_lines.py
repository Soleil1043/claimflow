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


def test_all_declared_lines_online() -> None:
    """四险种全部上线（T120，D053）；离线转人工仅剩 unknown（无 pack 产品类型）。"""
    assert online_lines() == frozenset({"medical", "auto", "property", "accident"})
    for line in ("auto", "property", "accident"):
        pack = get_line_pack(line)
        assert pack is not None and pack.online
        assert pack.required_docs, f"{line} pack 必备材料清单为空"
        assert pack.policy_terms, f"{line} pack 条款要素为空"
        assert pack.exclusion_keywords, f"{line} pack 除外关键词为空"


def test_liability_tools_per_line() -> None:
    """责任认定工具集按 pack 声明：医疗线带诊断匹配，其余仅条款检索（T120）。"""
    assert get_line_pack("medical").liability_tools == ("claim_rule_rag", "diagnosis_matcher")
    for line in ("auto", "property", "accident"):
        assert get_line_pack(line).liability_tools == ("claim_rule_rag",)


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
    """API 上传白名单 = 全部 pack doc_types 并集（T120 扩四险种）。"""
    expected = set()
    for pack in LINE_PACKS.values():
        expected |= set(pack.doc_types)
    assert all_doc_types() == frozenset(expected)
    assert {"police_report", "repair_invoice", "loss_assessment"} <= expected  # 车险
    assert {"incident_proof", "loss_list", "purchase_receipt"} <= expected  # 财产险
    assert len(LINE_PACKS) == 4
