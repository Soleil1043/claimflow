"""红队变异器单测（T159，D073）：确定性 / 溯源完整性 / 变体语义。"""

from __future__ import annotations

import json
from pathlib import Path

from evals.adjudication_metrics import load_adjudication_dataset
from evals.redteam import CLEAN_CORES, MUTATORS, generate_variants

DATASET_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication_adversarial.json"
HOLDOUT_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication_adversarial_holdout.json"


def _seed_dicts() -> list[dict]:
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return [c for c in raw["cases"] if c["expected"]["category"] == "injection"]


def test_mutators_have_framework_ref() -> None:
    """每条变异器必须带框架溯源（garak:/pyrit:/claimflow: 前缀）与家族归类。"""
    assert len(MUTATORS) >= 8
    for m in MUTATORS:
        assert m.framework_ref.startswith(("garak:", "pyrit:", "claimflow:")), m.mutator_id
        assert m.family in {"injection_override", "encoding", "authority", "compliance", "policy_dodge"}
        assert m.description


def test_mutators_deterministic() -> None:
    """同输入同输出（无 LLM 无随机数）：生成物可复现、可评审。"""
    for m in MUTATORS:
        probe = "阑尾炎手术费用15800元。"
        assert m.apply(probe) == m.apply(probe), m.mutator_id


def test_mutators_preserve_clean_core() -> None:
    """攻击包裹不得破坏干净事实核——干净语义仍在描述里，'注入不改变合法结论'才可判。"""
    core = "种植牙两颗费用12000元。"
    for m in MUTATORS:
        assert core in m.apply(core), m.mutator_id


def test_generate_variants_deterministic_and_inherits() -> None:
    """变体生成可复现；expected 逐字段继承种子；case_id 顺序稳定。"""
    seeds = _seed_dicts()
    v1, p1 = generate_variants(seeds)
    v2, p2 = generate_variants(seeds)
    assert v1 == v2
    assert p1 == p2
    assert len(v1) == len(seeds) * 3
    for v, p in zip(v1, p1.keys(), strict=True):
        assert v["case_id"] == p
        seed = next(s for s in seeds if s["case_id"] == p1[p]["source_case"])
        for key in ("route", "liability", "approved_amount"):
            assert v["expected"][key] == seed["expected"][key]


def test_holdout_file_matches_generator() -> None:
    """holdout 里的变体与变异器重生成结果一致（数据集未被手改漂移）。"""
    raw = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    committed = {c["case_id"]: c for c in raw["cases"] if c["case_id"].startswith("RTV-")}
    variants, provenance = generate_variants(_seed_dicts())
    assert set(committed) == set(provenance)
    for v in variants:
        assert committed[v["case_id"]] == v, f"{v['case_id']} 与重生成结果不一致（重跑 --generate）"


def test_clean_cores_match_adversarial_seeds() -> None:
    """干净事实核必须与种子描述的陈述部分逐字对应（防种子改写后失配）。"""
    adv, _, _ = load_adjudication_dataset(DATASET_PATH)
    for c in adv:
        assert c.case_id in CLEAN_CORES, f"{c.case_id} 缺干净事实核"
        assert CLEAN_CORES[c.case_id] in c.incident_description, (
            f"{c.case_id} 干净事实核与种子描述失配"
        )
