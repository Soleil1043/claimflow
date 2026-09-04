"""核赔金样本判分器（Phase 8 T088）。

纯函数判分：AdjudicationOutcome（评测运行器观测到的最终事实，T089 构造）
对照 AdjudicationExpected 逐维核对——路由（含 supplement kind 折叠）/ 金额
（Decimal 精确到分）/ 责任结论 / 险种分类 / worker 派发序列（按序子集）。

聚合产出与 T089 上线门对齐：金额正确率 100%（硬）、路由一致率 ≥95%（软）、
责任一致率 ≥90%（软）等阈值判断在 T089 落地，本模块只负责计算。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.schemas import AdjudicationCase, AdjudicationDataset, AdjudicationExpected

DATASET_PATH = Path(__file__).resolve().parent.parent / "evals" / "datasets" / "adjudication.json"


@dataclass
class AdjudicationOutcome:
    """评测运行器对单个案件的观测事实（由图执行结果构造）。"""

    route: str  # auto / human / supplement（human 时见 kind）
    kind: str | None = None  # review / escape  # review / escape（route=human 时）
    final_decision: str | None = None
    approved_amount: str | None = None
    liability_verdict: str | None = None
    case_type: str | None = None
    worker_sequence: list[str] = field(default_factory=list)
    error: str | None = None


def load_adjudication_dataset(path: Path | None = None) -> tuple[list[AdjudicationCase], dict[str, Any], list[dict[str, Any]]]:
    """装载数据集 → (cases, meta, frequency_signals)。"""
    raw = json.loads((path or DATASET_PATH).read_text(encoding="utf-8"))
    dataset = AdjudicationDataset.model_validate(raw)
    return dataset.cases, dataset.meta, dataset.frequency_signals


def route_match(expected: AdjudicationExpected, outcome: AdjudicationOutcome) -> bool:
    """路由一致：human 折叠 escape/review kind；supplement 看 supplement 挂起。"""
    if expected.route == "human":
        return outcome.route == "human"
    if expected.route == "supplement":
        return outcome.route == "supplement"
    return outcome.route == "auto"


def amount_match(expected: AdjudicationExpected, outcome: AdjudicationOutcome) -> bool:
    """金额精确一致（Decimal，到分）；期望未标注或观测失败 → True（不算金额错）。"""
    if expected.approved_amount is None or outcome.approved_amount is None:
        return True
    return Decimal(outcome.approved_amount) == Decimal(expected.approved_amount)


def liability_match(expected: AdjudicationExpected, outcome: AdjudicationOutcome) -> bool:
    if expected.liability is None or outcome.liability_verdict is None:
        return True
    return expected.liability == outcome.liability_verdict


def case_type_match(case: AdjudicationCase, outcome: AdjudicationOutcome) -> bool:
    if not outcome.case_type:
        return True
    return outcome.case_type == case.declared_case_type if case.declared_case_type else True


def sequence_contained(expected: list[str], actual: list[str]) -> bool:
    """期望 worker 序列是实际派发序列的按序子集（容忍合理重派/插空）。"""
    it = iter(actual)
    return all(stage in it for stage in expected)


def score_case(
    case: AdjudicationCase, outcome: AdjudicationOutcome
) -> dict[str, Any]:
    """单案判分：五维核对 + 汇总 matched（路由为 主判，金额错误一票否决）。"""
    checks = {
        "route": route_match(case.expected, outcome),
        "amount": amount_match(case.expected, outcome),
        "liability": liability_match(case.expected, outcome),
        "case_type": case_type_match(case, outcome),
        "sequence": sequence_contained(
            case.expected.expected_worker_sequence, outcome.worker_sequence
        )
        if case.expected.expected_worker_sequence
        else True,
    }
    return {
        "case_id": case.case_id,
        "category": case.expected.category,
        "expected_route": case.expected.route,
        "observed_route": outcome.route,
        "checks": checks,
        "matched": all(checks.values()),
        "error": outcome.error,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合：总一致率 + 分维度正确率 + 分类别一致率 + 失败明细。"""
    total = len(results)
    matched = sum(1 for r in results if r["matched"])

    def dim(name: str) -> float:
        scored = [r for r in results if r["checks"].get(name) is not None]
        if not scored:
            return 1.0
        return sum(1 for r in scored if r["checks"][name]) / len(scored)

    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        cat = r["category"]
        stat = by_category.setdefault(cat, {"total": 0, "matched": 0})
        stat["total"] += 1
        stat["matched"] += 1 if r["matched"] else 0

    failures = [
        {
            "case_id": r["case_id"],
            "category": r["category"],
            "expected_route": r["expected_route"],
            "observed_route": r["observed_route"],
            "checks": r["checks"],
            "error": r["error"],
        }
        for r in results
        if not r["matched"]
    ]

    return {
        "total": total,
        "matched": matched,
        "consistency": round(matched / total, 4) if total else 1.0,
        "dimensions": {
            "route": dim("route"),
            "amount": dim("amount"),
            "liability": dim("liability"),
            "case_type": dim("case_type"),
            "sequence": dim("sequence"),
        },
        "by_category": by_category,
        "failures": failures,
    }
