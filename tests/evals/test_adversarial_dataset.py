"""对抗回归集校验（T124）：独立数据集不污染主基线（D033 口径延续）。

结构约束：
- 两 tier 分明：injection（进硬门）≥8 条 / robustness（仅报告）≥6 条
- 注入类期望必须是"正确结果"（对抗操纵后的规格行为），不是"期望被拒"的悲观假设
- 鲁棒类期望必须是"正确判定"（同义词属于既有除外项，驱动 skill/关键词迭代）
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from evals.adjudication_metrics import load_adjudication_dataset

DATASET_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication_adversarial.json"
MAIN_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication.json"


def test_dataset_loads_and_tier_split() -> None:
    cases, meta, _ = load_adjudication_dataset(DATASET_PATH)
    by_tier: dict[str, list] = {}
    for c in cases:
        by_tier.setdefault(c.expected.category, []).append(c)
    assert len(by_tier["injection"]) >= 8
    assert len(by_tier["robustness"]) >= 6
    assert set(by_tier) == {"injection", "robustness"}
    assert "tiers" in meta  # tier 语义随数据集自描述


def test_injection_tier_expects_correct_outcomes() -> None:
    """注入类期望=规格正确结果：不得出现"期望被操纵后行为"的用例。"""
    cases, _, _ = load_adjudication_dataset(DATASET_PATH)
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
    cases, _, _ = load_adjudication_dataset(DATASET_PATH)
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


def test_adversarial_ids_unique_prefixed() -> None:
    cases, _, _ = load_adjudication_dataset(DATASET_PATH)
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))
    assert all(cid.startswith("ADV-") for cid in ids)
