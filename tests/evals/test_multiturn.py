"""T070 多轮对话评测测试：schema 校验、run_case 逐轮同 thread、末轮判分。"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evals.schemas import EvalCase, EvalCategory, EvalDataset
from evals.test_suite import DATASETS, run_case

DATASET_PATH = DATASETS["multiturn"]


class _FakeGraph:
    """假主图：记录每次 ainvoke 的输入与 thread，末次返回拼接回答。

    模拟 checkpoint 行为：每轮调用只收到当轮 messages（首轮带初始 state，
    后续轮增量），我们按调用顺序记录，用于断言轮次/同 thread/判分口径。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage

        utterance = next(m.content for m in payload["messages"] if isinstance(m, HumanMessage))
        self.calls.append(
            {
                "utterance": utterance,
                "thread_id": config["configurable"]["thread_id"],
                "has_base_state": "task_plan" in payload,
            }
        )
        return {
            "messages": payload["messages"],
            "answer": f"回答（{utterance[:6]}）4640 元",
            "intent": "complex_consult",
            "compliance_status": "PASS",
            "need_human_intervention": False,
        }


def test_multiturn_dataset_valid() -> None:
    """数据集 schema 校验：30 条、每条恰好多轮、ID 前缀。"""
    ds = EvalDataset.model_validate_json(DATASET_PATH.read_text(encoding="utf-8"))
    assert len(ds.cases) == 30
    for case in ds.cases:
        assert case.id.startswith("MT-")
        assert len(case.turns) >= 2, f"{case.id} 至少两轮"
        assert all(t.strip() for t in case.turns), f"{case.id} 存在空轮次"
        assert case.user_input == case.turns[0], f"{case.id} user_input 应等于首轮（摘要兼容）"


def test_turns_default_empty_single_turn() -> None:
    """turns 默认空=单轮（向后兼容：老数据集零改动通过校验）。"""
    case = EvalCase.model_validate(
        {"id": "S-1", "category": EvalCategory.SIMPLE_FAQ, "user_input": "q", "any_of": ["a"]}
    )
    assert case.turns == []


def test_turns_rejects_empty_items() -> None:
    """turns 显式传空列表被拒（空轮次无意义）。"""
    with pytest.raises(ValidationError, match="turns"):
        EvalCase.model_validate(
            {
                "id": "S-2",
                "category": EvalCategory.SIMPLE_FAQ,
                "user_input": "q",
                "any_of": ["a"],
                "turns": [],
            }
        )


@pytest.mark.asyncio
async def test_run_case_multi_turn_same_thread() -> None:
    """多轮执行：逐轮 ainvoke 同一 thread、首轮带初始 state、末轮回答判分。"""
    case = EvalCase.model_validate(
        {
            "id": "MT-T1",
            "category": EvalCategory.MULTI_STEP,
            "user_input": "第一轮",
            "turns": ["第一轮", "第二轮"],
            "expected_numbers": ["4640"],
        }
    )
    graph = _FakeGraph()
    cr = await run_case(graph, case)
    assert len(graph.calls) == 2
    assert {c["thread_id"] for c in graph.calls} == {"eval-MT-T1"}
    assert graph.calls[0]["has_base_state"] is True
    assert graph.calls[1]["has_base_state"] is False  # 后续轮增量输入
    assert graph.calls[1]["utterance"] == "第二轮"
    assert cr.passed is True  # 末轮 answer（4640）满足数值断言
    assert cr.actual_intent == "complex_consult"


@pytest.mark.asyncio
async def test_run_case_single_turn_unchanged() -> None:
    """单轮（无 turns）行为不变：一次 ainvoke。"""
    case = EvalCase.model_validate(
        {"id": "S-3", "category": EvalCategory.SIMPLE_FAQ, "user_input": "q", "any_of": ["4640"]}
    )
    graph = _FakeGraph()
    await run_case(graph, case)
    assert len(graph.calls) == 1
    assert graph.calls[0]["utterance"] == "q"
