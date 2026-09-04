"""核赔金样本判分器测试（T088）：五维核对 / 聚合 / 数据集自洽。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from evals.adjudication_metrics import (
    aggregate,
    amount_match,
    liability_match,
    load_adjudication_dataset,
    route_match,
    score_case,
    sequence_contained,
)
from evals.schemas import AdjudicationCase, AdjudicationExpected

DATASET_PATH = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "adjudication.json"


def _case(route="auto", liability="covered", amount="4640.00",
          category="normal", sequence=None, case_type="medical") -> AdjudicationCase:
    return AdjudicationCase(
        case_id="C1", user_id="u", policy_no="P", claimed_amount="15800.00",
        incident_date="2026-08-10", incident_description="x",
        expected=AdjudicationExpected(
            category=category, route=route, liability=liability,
            approved_amount=amount,
            expected_worker_sequence=sequence or [],
        ),
    )


def _outcome(route="auto", decision="approved", amount="4640.00",
             liability="covered", case_type="medical", sequence=None, error=None,
             kind=None):
    from evals.adjudication_metrics import AdjudicationOutcome

    return AdjudicationOutcome(
        route=route, kind=kind, final_decision=decision, approved_amount=amount,
        liability_verdict=liability, case_type=case_type,
        worker_sequence=sequence or [], error=error,
    )


# ---------- 单维判分 ----------


def test_route_match_human_folds_escape() -> None:
    """human 期望与 escape/review kind 的观测折叠一致。"""
    expected = _case(route="human").expected
    assert route_match(expected, _outcome(route="human", kind="review"))
    assert route_match(expected, _outcome(route="human", kind="escape"))
    assert not route_match(expected, _outcome(route="auto"))


def test_route_match_supplement() -> None:
    expected = _case(route="supplement").expected
    assert route_match(expected, _outcome(route="supplement"))
    assert not route_match(expected, _outcome(route="human"))


def test_route_match_auto() -> None:
    expected = _case(route="auto").expected
    assert route_match(expected, _outcome(route="auto"))
    assert not route_match(expected, _outcome(route="human"))


def test_amount_match_decimal_exact() -> None:
    """金额 Decimal 精确匹配（4640.00 ≠ 4640.01）。"""
    expected = _case(amount="4640.00").expected
    assert amount_match(expected, _outcome(amount="4640.00"))
    assert not amount_match(expected, _outcome(amount="4640.01"))
    # 期望未标注 → True（不算金额错）
    assert amount_match(_case(amount=None).expected, _outcome(amount=None))


def test_liability_match() -> None:
    expected = _case(liability="not_covered").expected
    assert liability_match(expected, _outcome(liability="not_covered"))
    assert not liability_match(expected, _outcome(liability="covered"))


def test_sequence_contained_subsequence() -> None:
    """期望序列是实际序列的按序子集（容忍插空）。"""
    assert sequence_contained(
        ["material_review", "policy_verify", "amount_calc"],
        ["material_review", "policy_verify", "fraud_check", "liability_judge", "amount_calc"],
    )
    assert not sequence_contained(
        ["amount_calc", "material_review"],
        ["material_review", "amount_calc"],
    )


# ---------- score_case 汇总 ----------


def test_score_case_all_pass() -> None:
    case = _case(sequence=["material_review", "policy_verify"])
    outcome = _outcome(sequence=["material_review", "orchestrator", "policy_verify"])
    result = score_case(case, outcome)
    assert result["matched"] is True
    assert all(result["checks"].values())


def test_score_case_amount_error_vetoes() -> None:
    """金额错误一票否决（路由对但金额错 → matched=False）。"""
    case = _case(amount="4640.00")
    outcome = _outcome(route="auto", amount="4600.00")
    result = score_case(case, outcome)
    assert result["checks"]["route"] is True
    assert result["checks"]["amount"] is False
    assert result["matched"] is False


# ---------- 聚合 ----------


def test_aggregate_dimensions_and_failures() -> None:
    results = [
        score_case(_case(), _outcome()),
        score_case(_case(amount="4640.00"), _outcome(amount="4600.00")),
        score_case(_case(route="human"), _outcome(route="auto")),
    ]
    agg = aggregate(results)
    assert agg["total"] == 3
    assert agg["matched"] == 1
    assert agg["dimensions"]["amount"] == pytest.approx(2 / 3)
    assert agg["dimensions"]["route"] == pytest.approx(2 / 3)
    assert len(agg["failures"]) == 2


# ---------- 数据集自洽 ----------


def test_dataset_loads_and_count() -> None:
    cases, meta, freq = load_adjudication_dataset()
    assert len(cases) >= 150
    assert len(freq) > 0
    assert "金额公式" in json.dumps(meta, ensure_ascii=False)


def test_dataset_category_coverage() -> None:
    cases, _, _ = load_adjudication_dataset()
    by_cat = {c.expected.category for c in cases}
    required = {"normal", "rejected", "partial", "fraud", "edge", "missing", "intake"}
    assert required <= by_cat, f"缺少分类：{required - by_cat}"


def test_dataset_auto_cases_amount_within_threshold() -> None:
    """auto 路由的案件核定金额 ≤ 自动签发线（5000）或为 0（拒赔）。"""
    cases, _, _ = load_adjudication_dataset()
    for c in cases:
        if c.expected.route == "auto" and c.expected.approved_amount:
            assert Decimal(c.expected.approved_amount) <= Decimal("5000.00"), (
                f"{c.case_id} auto 超阈值：{c.expected.approved_amount}"
            )


def test_dataset_human_threshold_above_limit() -> None:
    """human 路由的正常类案件核定金额 > 自动签发线。"""
    cases, _, _ = load_adjudication_dataset()
    for c in cases:
        if (c.expected.route == "human" and c.expected.category == "normal"
                and c.expected.approved_amount):
            assert Decimal(c.expected.approved_amount) > Decimal("5000.00"), (
                f"{c.case_id} normal/human 但金额 ≤5000：{c.expected.approved_amount}"
            )


def test_dataset_unique_ids() -> None:
    cases, _, _ = load_adjudication_dataset()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_dataset_amounts_two_decimals() -> None:
    """所有金额精确到分（两位小数）。"""
    cases, _, _ = load_adjudication_dataset()
    for c in cases:
        if c.expected.approved_amount is not None:
            parts = c.expected.approved_amount.split(".")
            assert len(parts) == 2 and len(parts[1]) == 2, (
                f"{c.case_id} 金额非两位小数：{c.expected.approved_amount}"
            )


def test_dataset_waiting_reject_zero_amount() -> None:
    """等待期拒赔案核定金额固定 0.00。"""
    cases, _, _ = load_adjudication_dataset()
    waiting = [c for c in cases if "等待期" in (c.expected.note or "")
               and c.expected.route == "auto"
               and c.expected.liability == "not_covered"]
    assert waiting, "应存在等待期拒赔案"
    for c in waiting:
        assert c.expected.approved_amount == "0.00", f"{c.case_id} 等待期拒赔金额非 0"
