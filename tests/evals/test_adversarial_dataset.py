"""对抗回归集校验（T124 立项；T159 拆分后口径）。

结构约束：
- adversarial.json：仅 injection tier ≥8 条（push CI 硬门，安全语义不可松）
- adversarial_holdout.json：robustness ≥6 条（自 adversarial 移入的盲测层）
  + 红队变体 ≥12 条（evals/redteam.py 生成，期望逐字段继承种子案）
- 注入类期望必须是"正确结果"（对抗操纵后的规格行为），不是"期望被拒"的悲观假设
- 鲁棒类期望必须是"正确判定"（同义词属于既有除外项，驱动 skill/关键词迭代）
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from evals.adjudication_metrics import load_adjudication_dataset

DATASET_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication_adversarial.json"
HOLDOUT_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication_adversarial_holdout.json"
MAIN_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication.json"


def test_adversarial_injection_only_tier() -> None:
    """T159 拆分后：adversarial.json 仅 injection 层（robustness 已移入 holdout）。"""
    cases, meta, _ = load_adjudication_dataset(DATASET_PATH)
    tiers = {c.expected.category for c in cases}
    assert tiers == {"injection"}
    assert len(cases) >= 8
    assert "tiers" in meta  # tier 语义随数据集自描述


def test_holdout_composition() -> None:
    """holdout：robustness 种子 ≥6 + 红队变体 ≥12，provenance 溯源完整。"""
    cases, meta, _ = load_adjudication_dataset(HOLDOUT_PATH)
    robust = [c for c in cases if c.expected.category == "robustness"]
    variants = [c for c in cases if c.case_id.startswith("RTV-")]
    assert len(robust) >= 6
    assert len(variants) >= 12
    assert len(robust) + len(variants) == len(cases)
    prov = meta.get("provenance", {})
    for v in variants:
        assert v.case_id in prov, f"{v.case_id} 缺溯源"
        assert prov[v.case_id]["source_case"].startswith("ADV-")
        assert prov[v.case_id]["framework_ref"]
        assert prov[v.case_id]["mutator"]
    assert meta.get("discipline")  # 盲测纪律随数据集自描述


def test_injection_tier_expects_correct_outcomes() -> None:
    """注入类期望=规格正确结果：不得出现"期望被操纵后行为"的用例（两数据集一致口径）。"""
    for path in (DATASET_PATH, HOLDOUT_PATH):
        cases, _, _ = load_adjudication_dataset(path)
        for c in cases:
            if c.expected.category != "injection":
                continue
            assert c.expected.route == "auto", f"{c.case_id} 注入类应走自动路径（被操纵才是异常）"
            assert c.expected.liability in {"covered", "partial", "not_covered"}
            # 期望金额非零时必须是规格公式值（四位金额字符串）
            if c.expected.approved_amount and Decimal(c.expected.approved_amount) > 0:
                assert Decimal(c.expected.approved_amount) == Decimal(c.expected.approved_amount).quantize(
                    Decimal("0.01")
                )


def test_robustness_tier_targets_known_synonym_gaps() -> None:
    """鲁棒类全部期望 not_covered（同义词均属既有除外项），且与主基线零案件号重叠。"""
    cases, _, _ = load_adjudication_dataset(HOLDOUT_PATH)
    main_ids = {
        c["case_id"]
        for c in json.loads(MAIN_PATH.read_text(encoding="utf-8"))["cases"]
    }
    for c in cases:
        if c.expected.category != "robustness":
            continue
        assert c.expected.liability == "not_covered", f"{c.case_id} 同义词案应期望拒赔"
        assert c.expected.approved_amount == "0.00"
        assert c.case_id not in main_ids, "对抗集与主基线案件号不得重叠"


def test_variant_expected_inherits_seed() -> None:
    """红队变体期望逐字段继承种子案（注入变体必须 behave like 干净案）。"""
    adv, _, _ = load_adjudication_dataset(DATASET_PATH)
    hold, meta, _ = load_adjudication_dataset(HOLDOUT_PATH)
    seed_by_id = {c.case_id: c for c in adv}
    prov = meta.get("provenance", {})
    for c in hold:
        if not c.case_id.startswith("RTV-"):
            continue
        seed = seed_by_id[prov[c.case_id]["source_case"]]
        assert c.expected.route == seed.expected.route
        assert c.expected.liability == seed.expected.liability
        assert c.expected.approved_amount == seed.expected.approved_amount


def test_ids_unique_prefixed_across_datasets() -> None:
    """两数据集案件号全局唯一：ADV- 前缀（原件）/ RTV- 前缀（变体）。"""
    adv, _, _ = load_adjudication_dataset(DATASET_PATH)
    hold, _, _ = load_adjudication_dataset(HOLDOUT_PATH)
    ids = [c.case_id for c in adv] + [c.case_id for c in hold]
    assert len(ids) == len(set(ids))
    assert all(cid.startswith(("ADV-", "RTV-")) for cid in ids)
