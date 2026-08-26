"""T033 关联类评测集测试：规模、溯源、图谱可命中性（纯检索层验证）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.schemas import EvalCategory, EvalDataset
from services.rag.graph_retriever import reset_knowledge_graph, search_graph

DATASET_PATH = Path("evals/datasets/eval_graph_assoc.json")


@pytest.fixture(scope="module")
def dataset() -> EvalDataset:
    return EvalDataset.model_validate_json(DATASET_PATH.read_text(encoding="utf-8"))


def test_graph_assoc_dataset_valid(dataset: EvalDataset) -> None:
    """schema 校验通过，规模 ≥20。"""
    assert len(dataset.cases) >= 20
    assert all(
        c.category in (EvalCategory.SIMPLE_FAQ, EvalCategory.MULTI_STEP) for c in dataset.cases
    )


def test_graph_assoc_ids_unique(dataset: EvalDataset) -> None:
    ids = [c.id for c in dataset.cases]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("GA-") for i in ids)


def test_graph_assoc_traceable(dataset: EvalDataset) -> None:
    """每条用例 note 引用 kb 文档（溯源要求）。"""
    for case in dataset.cases:
        assert "kb" in case.note, f"{case.id} note 未引用 kb_docs 来源"


async def test_graph_assoc_entity_linkable(dataset: EvalDataset) -> None:
    """关键验收：用例查询在真实图谱上实体链接可命中（保证混合召回有信号源）。

    不要求 100%（部分规则型问题本就不含实体），但关联问题主体应可命中；
    阈值 15/24 是对当前图谱召回下限的量化（低于此说明图谱/链接退化）。
    """
    reset_knowledge_graph()
    try:
        hit = 0
        for case in dataset.cases:
            result = await search_graph(case.user_input)
            if result.matched_entities:
                hit += 1
        assert hit >= 15, f"实体链接命中不足：{hit}/{len(dataset.cases)}"
    finally:
        reset_knowledge_graph()
