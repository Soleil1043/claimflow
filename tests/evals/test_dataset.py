"""T026 评测数据集校验测试：规模、配比、schema、标注质量。"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.schemas import EvalCase, EvalCategory, EvalDataset

DATASET_PATH = Path("evals/datasets/eval_dataset.json")


@pytest.fixture(scope="module")
def dataset() -> EvalDataset:
    return EvalDataset.model_validate_json(DATASET_PATH.read_text(encoding="utf-8"))


def test_dataset_exists_and_valid(dataset: EvalDataset) -> None:
    """数据集文件存在且通过 schema 校验。"""
    assert dataset.version
    assert len(dataset.cases) == 200


def test_category_ratio(dataset: EvalDataset) -> None:
    """主数据集配比符合架构 9.2：FAQ 30 / 单领域 60 / 多步 80 / 边界 30。

    graph_assoc（T033 关联类）在独立数据集 eval_graph_assoc.json，不占主数据集配比。
    """
    counts = {c.value: 0 for c in EvalCategory}
    for case in dataset.cases:
        counts[case.category] += 1
    assert {k: v for k, v in counts.items() if v} == {
        "simple_faq": 30,
        "single_domain": 60,
        "multi_step": 80,
        "edge_case": 30,
    }


def test_case_ids_unique_and_prefixed(dataset: EvalDataset) -> None:
    """ID 唯一且与分类前缀一致。"""
    ids = [c.id for c in dataset.cases]
    assert len(ids) == len(set(ids))
    prefix_map = {
        EvalCategory.SIMPLE_FAQ: "FAQ",
        EvalCategory.SINGLE_DOMAIN: ("POL", "MED", "CMP"),
        EvalCategory.MULTI_STEP: "MS",
        EvalCategory.EDGE_CASE: "EDGE",
    }
    for case in dataset.cases:
        prefixes = prefix_map[case.category]
        if isinstance(prefixes, str):
            prefixes = (prefixes,)
        assert case.id.startswith(prefixes), f"{case.id} 前缀与分类不符"


def test_annotation_quality(dataset: EvalDataset) -> None:
    """每条用例有判分要点与标注说明；期望值可溯源（note 非空）。"""
    for case in dataset.cases:
        assert case.user_input.strip(), f"{case.id} 输入为空"
        assert case.note, f"{case.id} 缺少标注说明（期望值溯源要求）"


def test_calc_anchor_cases(dataset: EvalDataset) -> None:
    """kb03 计算锚点用例存在：期望回答含 4640。"""
    anchors = [
        c
        for c in dataset.cases
        if "4640" in (c.must_include or []) or "4,640" in (c.must_include or [])
    ]
    assert len(anchors) >= 3, "计算锚点用例（kb03 示例 4640 元）不足 3 条"


def test_compliance_redline_cases(dataset: EvalDataset) -> None:
    """合规红线用例存在：must_not_include 约束违规话术。"""
    redlines = [c for c in dataset.cases if c.must_not_include]
    assert len(redlines) >= 4, "must_not_include 红线用例不足 4 条"


def test_expected_tools_are_registered_names(dataset: EvalDataset) -> None:
    """期望工具名必须是系统真实注册的工具（防止评测器永远失分）。"""
    registered = {
        "policy_query",
        "claim_calculator",
        "claim_rule_rag",
        "claim_status_query",
        "medical_record_query",
        "diagnosis_matcher",
        "ocr_extract",
        "compliance_rule_check",
        "sensitive_filter",
        "risk_scoring",
    }
    for case in dataset.cases:
        for tool in case.expected_tools:
            assert tool in registered, f"{case.id} 期望了未注册的工具 {tool}"


def test_case_schema_rejects_unscorable() -> None:
    """无判分要点的用例被 schema 拒绝（防呆）。"""
    with pytest.raises(Exception, match="缺少判分要点"):
        EvalCase(id="BAD-001", category=EvalCategory.SIMPLE_FAQ, user_input="无要点用例")
