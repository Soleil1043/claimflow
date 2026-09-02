"""T068 LLM-as-judge 测试：rubric 结构、判过阈值、fail-open、聚合口径。"""

from __future__ import annotations

from typing import Any

import pytest

from evals.judge import (
    JUDGE_PASS_THRESHOLD,
    JudgeVerdict,
    judge_case,
    judge_results,
    needs_judge,
    verdict_to_record,
)
from evals.metrics import CaseResult, aggregate
from evals.schemas import EvalCase, EvalCategory


def _case(**kwargs: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": "J-001",
        "category": EvalCategory.SIMPLE_FAQ,
        "user_input": "等待期多久",
        "any_of": ["30天"],
    }
    base.update(kwargs)
    return EvalCase.model_validate(base)


# ---------- 范围与结构 ----------


def test_needs_judge_scope() -> None:
    """judge 范围：must_include 为空的用例（缺严格断言层）。"""
    assert needs_judge(_case()) is True
    assert needs_judge(_case(must_include=["30天"])) is False


def test_verdict_record_structure_and_threshold() -> None:
    """verdict → 记录：三维 + 总分 + 阈值判过（≥4）。"""
    high = verdict_to_record(
        JudgeVerdict(faithfulness=2, completeness=2, compliance=2, rationale="准确完整")
    )
    assert high["total"] == 6 and high["pass"] is True
    low = verdict_to_record(
        JudgeVerdict(faithfulness=1, completeness=1, compliance=1, rationale="平庸")
    )
    assert low["total"] == 3 and low["pass"] is False
    assert JUDGE_PASS_THRESHOLD == 4


# ---------- judge_case：mock LLM 与 fail-open ----------


class _FakeStructuredModel:
    """假 with_structured_output 模型：可注入返回值或抛异常。"""

    def __init__(self, retval: Any = None, error: Exception | None = None) -> None:
        self._retval = retval
        self._error = error
        self.calls = 0

    def with_structured_output(self, _schema: type, **_kwargs: Any) -> _FakeStructuredModel:
        return self

    async def ainvoke(self, _messages: Any) -> Any:
        self.calls += 1
        if self._error:
            raise self._error
        return self._retval


@pytest.mark.asyncio
async def test_judge_case_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径：LLM 返回 verdict → 结构化记录。"""
    fake = _FakeStructuredModel(
        JudgeVerdict(faithfulness=2, completeness=1, compliance=2, rationale="尚可")
    )
    monkeypatch.setattr(
        "services.llm.client.get_chat_model",
        lambda temperature=0.0: fake,
    )
    record = await judge_case(_case(), "等待期为 30 天，投保后 30 天内确诊不赔。")
    assert record is not None
    assert record["total"] == 5 and record["pass"] is True
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_judge_case_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 异常 → None（fail-open，不阻塞评测主流程）。"""
    fake = _FakeStructuredModel(error=RuntimeError("LLM timeout"))
    monkeypatch.setattr("services.llm.client.get_chat_model", lambda temperature=0.0: fake)
    record = await judge_case(_case(), "回答内容")
    assert record is None


@pytest.mark.asyncio
async def test_judge_case_empty_answer_skipped() -> None:
    """空回答不 judge（转人工/失败用例无答案可判）。"""
    assert await judge_case(_case(), "  ") is None


@pytest.mark.asyncio
async def test_judge_results_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """批量：只 judge 范围内用例；按 case_id 返回。"""
    fake = _FakeStructuredModel(
        JudgeVerdict(faithfulness=2, completeness=2, compliance=2)
    )
    monkeypatch.setattr("services.llm.client.get_chat_model", lambda temperature=0.0: fake)
    cases = [
        _case(id="A-1"),
        _case(id="A-2"),
        _case(id="A-3", must_include=["锚点"]),  # 范围外
    ]
    results = [
        {"case_id": "A-1", "answer": "回答 1"},
        {"case_id": "A-2", "answer": ""},
        {"case_id": "A-3", "answer": "回答 3"},
    ]
    out = await judge_results(cases, results)  # type: ignore[arg-type]
    assert set(out) == {"A-1"}


# ---------- 聚合口径 ----------


def test_aggregate_judge_independent_of_passed() -> None:
    """judge 独立列：不并入 passed；判过率分母只计 judge 产出用例。"""
    results = [
        CaseResult(
            case_id="A-1",
            category=EvalCategory.SIMPLE_FAQ,
            passed=False,  # 关键词层 FAIL
            judge={"faithfulness": 2, "completeness": 2, "compliance": 2, "total": 6, "pass": True},
        ),
        CaseResult(
            case_id="A-2",
            category=EvalCategory.SIMPLE_FAQ,
            passed=True,
            judge={"faithfulness": 1, "completeness": 1, "compliance": 1, "total": 3, "pass": False},
        ),
        CaseResult(case_id="A-3", category=EvalCategory.SIMPLE_FAQ, passed=True),
    ]
    report = aggregate(results)
    assert report.judge_scored == 2
    assert report.judge_pass_rate == 0.5
    assert report.passed == 2  # judge 不影响 passed


def test_aggregate_judge_none_when_unused() -> None:
    """未启用 judge → judge_pass_rate=None（未考核），不误导为 0 或 1。"""
    report = aggregate([CaseResult(case_id="A", category=EvalCategory.SIMPLE_FAQ)])
    assert report.judge_pass_rate is None
    assert report.judge_scored == 0
