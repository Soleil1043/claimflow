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
    assert len(dataset.cases) == 218


def test_category_ratio(dataset: EvalDataset) -> None:
    """主数据集配比符合架构 9.2：FAQ 30 / 单领域 60 / 多步 80 / 边界 30 / 转人工 18。

    graph_assoc（T033 关联类）在独立数据集 eval_graph_assoc.json，不占主数据集配比；
    human_handoff（T065，v1.1.0）并入主数据集——北极星指标须在全量报告直接可见（D033）。
    """
    counts = {c.value: 0 for c in EvalCategory}
    for case in dataset.cases:
        counts[case.category] += 1
    assert {k: v for k, v in counts.items() if v} == {
        "simple_faq": 30,
        "single_domain": 60,
        "multi_step": 80,
        "edge_case": 30,
        "human_handoff": 18,
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
        EvalCategory.HUMAN_HANDOFF: "HITL",
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
    """kb03 计算锚点用例存在：期望回答含精确数值 4640（T067 迁移至 expected_numbers）。"""
    anchors = [c for c in dataset.cases if "4640" in (c.expected_numbers or [])]
    assert len(anchors) >= 3, "计算锚点用例（kb03 示例 4640 元）不足 3 条"


def test_compliance_redline_cases(dataset: EvalDataset) -> None:
    """合规红线用例存在：must_not_include 约束违规话术。"""
    redlines = [c for c in dataset.cases if c.must_not_include]
    assert len(redlines) >= 4, "must_not_include 红线用例不足 4 条"


def test_human_handoff_cases(dataset: EvalDataset) -> None:
    """转人工期望用例（T065，BUG-001 分母）：全部期望转人工且带违规承诺红线。"""
    handoff = [c for c in dataset.cases if c.category == EvalCategory.HUMAN_HANDOFF]
    assert len(handoff) >= 15, "转人工期望用例不足 15 条（北极星分母）"
    for case in handoff:
        assert case.expect_human_intervention is True, f"{case.id} 未期望转人工"
        assert case.must_not_include, f"{case.id} 缺 must_not_include 违规承诺红线"


def test_expected_tools_are_registered_names(dataset: EvalDataset) -> None:
    """期望工具名必须是系统真实注册的工具（防止评测器永远失分）。

    以 tools.factory.get_default_tool_map() 为准（T069 修正：原硬编码名单里的
    medical_record_query/claim_status_query 为历史笔误，真实注册名是 record_query，
    claim_status_query 未注册——工厂名单是唯一事实源）。
    """
    from tools.factory import get_default_tool_map

    registered = set(get_default_tool_map())
    for case in dataset.cases:
        for tool in case.expected_tools:
            assert tool in registered, f"{case.id} 期望了未注册的工具 {tool}"


def test_case_schema_rejects_unscorable() -> None:
    """无判分要点的用例被 schema 拒绝（防呆）。"""
    with pytest.raises(Exception, match="缺少判分要点"):
        EvalCase(id="BAD-001", category=EvalCategory.SIMPLE_FAQ, user_input="无要点用例")


def test_adversarial_dataset_valid() -> None:
    """安全对抗集（T071，GAP-005）：20 条五类，红线断言具体违规输出物。"""
    from evals.schemas import EvalDataset

    ds = EvalDataset.model_validate_json(
        Path("evals/datasets/eval_adversarial.json").read_text(encoding="utf-8")
    )
    assert len(ds.cases) == 20
    assert all(c.category == EvalCategory.ADVERSARIAL for c in ds.cases)
    # 注入/越权/PII/承诺类（前 16 条）：必须有 must_not_include 红线
    for case in ds.cases[:16]:
        assert case.must_not_include, f"{case.id} 缺 must_not_include 红线断言"
    # 鲁棒性类（后 4 条）：期望正常服务，有正向判分要点
    for case in ds.cases[16:]:
        assert case.expected_numbers or case.any_of, f"{case.id} 缺正向判分要点"


def test_multiturn_dataset_registered() -> None:
    """多轮集（T070）已注册进 DATASETS（评测台 /meta 可见）。"""
    from evals.test_suite import DATASETS

    assert "multiturn" in DATASETS and DATASETS["multiturn"].exists()
    assert "adversarial" in DATASETS and DATASETS["adversarial"].exists()


def test_trajectory_annotation_coverage(dataset: EvalDataset) -> None:
    """轨迹标注覆盖（T069，GAP-003）：multi_step order ≥60、route ≥30、合计 ≥80。

    未标满 80 的部分有原则：纯 RAG 咨询 task_plan 为空（route 无从考核）、
    工具二义（claim_rule_rag 两 Agent 均持有）、个别无工具用例。
    """
    ms = [c for c in dataset.cases if c.category == EvalCategory.MULTI_STEP]
    order = sum(1 for c in ms if c.expected_tool_order)
    route = sum(1 for c in ms if c.expected_route)
    assert order >= 60, f"order 标注不足：{order}"
    assert route >= 30, f"route 标注不足：{route}"
    assert order + route >= 80
    # 有 order 的用例必须同时有 expected_tools（order 是其排序投影）
    for c in ms:
        if c.expected_tool_order:
            assert c.expected_tools, f"{c.id} 有 order 无 expected_tools"
            assert set(c.expected_tool_order) == set(c.expected_tools)
