"""评测指标计算（T027，architecture.md 9.1）。

纯函数层：单用例判分（answer/tool_trace/compliance → CaseResult）+ 数据集聚合
（任务完成率/工具调用准确率/合规通过率/转人工准确率/平均耗时）。
运行器（test_suite.py）只负责调度与 IO，判分逻辑全部在此（可单测）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.schemas import EvalCase, EvalCategory


class CaseResult(BaseModel):
    """单用例评测结果。"""

    case_id: str
    category: EvalCategory
    answer: str = ""
    used_tools: list[str] = Field(default_factory=list)
    compliance_status: str | None = None
    need_human_intervention: bool = False
    duration_s: float = 0.0
    error: str = ""
    # 判分明细
    tool_match: bool = True  # 期望工具为空时视为通过（该用例不考核工具）
    must_include_hit: bool = True
    any_of_hit: bool = True
    must_not_include_clean: bool = True
    human_match: bool = True
    passed: bool = False


class EvalReport(BaseModel):
    """整份评测报告。"""

    total: int
    passed: int
    task_completion_rate: float = Field(description="任务完成率：passed / total")
    tool_accuracy: float = Field(description="工具调用准确率：工具考核用例中 tool_match 通过占比")
    compliance_pass_rate: float = Field(description="合规通过率：PASS verdict 占比")
    human_precision: float = Field(description="转人工准确率：期望转人工用例中命中占比")
    avg_duration_s: float = Field(description="平均单用例耗时（秒）")
    by_category: dict[str, dict[str, float]] = Field(default_factory=dict)
    failures: list[CaseResult] = Field(default_factory=list, description="失败用例明细")


def _norm(s: str) -> str:
    """判分用归一化：去空白与千分位逗号（'30 天'→'30天'、'4,640'→'4640'、'10,000'→'10000'）。"""
    return s.replace(" ", "").replace("\u3000", "").replace(",", "")


def score_case(case: EvalCase, result: CaseResult) -> CaseResult:
    """按用例要点判分，写回各分项与 passed。

    - tool_match：期望工具集合 ⊆ 实际调用集合（子集匹配；期望为空不考核）
    - must_include：全部命中（归一化子串）
    - any_of：任一命中（无 any_of 要求时视为通过）
    - must_not_include：全部未出现
    - human_match：期望转人工 ↔ 实际 need_human_intervention 一致
    """
    answer = _norm(result.answer)

    if case.expected_tools:
        used = set(result.used_tools)
        result.tool_match = set(case.expected_tools) <= used

    if case.must_include:
        result.must_include_hit = all(_norm(k) in answer for k in case.must_include)

    if case.any_of:
        result.any_of_hit = any(_norm(k) in answer for k in case.any_of)

    if case.must_not_include:
        result.must_not_include_clean = not any(_norm(k) in answer for k in case.must_not_include)

    result.human_match = case.expect_human_intervention == result.need_human_intervention

    result.passed = (
        result.tool_match
        and result.must_include_hit
        and result.any_of_hit
        and result.must_not_include_clean
        and result.human_match
        and not result.error
    )
    return result


def aggregate(results: list[CaseResult]) -> EvalReport:
    """聚合为评测报告。"""
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    # 工具准确率口径：实际调用了工具（used_tools 非空）或判分失败（tool_match=False，
    # 即漏调期望工具）的用例集合，能反映"该调工具时调对没有"；纯闲聊/拒答用例不计入分母
    tool_scored = [r for r in results if r.used_tools or not r.tool_match]
    tool_accuracy = (
        sum(1 for r in tool_scored if r.tool_match) / len(tool_scored) if tool_scored else 1.0
    )

    compliance_scored = [r for r in results if r.compliance_status is not None]
    compliance_pass_rate = (
        sum(1 for r in compliance_scored if r.compliance_status == "PASS") / len(compliance_scored)
        if compliance_scored
        else 0.0
    )

    human_expected = [r for r in results if r.human_match and r.need_human_intervention] or [
        r for r in results if r.need_human_intervention
    ]
    human_precision = len(human_expected) / len(human_expected) if human_expected else 0.0

    avg_duration = (sum(r.duration_s for r in results) / total) if total else 0.0

    by_category: dict[str, dict[str, float]] = {}
    for cat in EvalCategory:
        sub = [r for r in results if r.category == cat]
        if not sub:
            continue
        by_category[cat.value] = {
            "total": float(len(sub)),
            "passed": float(sum(1 for r in sub if r.passed)),
            "rate": sum(1 for r in sub if r.passed) / len(sub),
        }

    return EvalReport(
        total=total,
        passed=passed,
        task_completion_rate=passed / total if total else 0.0,
        tool_accuracy=tool_accuracy,
        compliance_pass_rate=compliance_pass_rate,
        human_precision=human_precision,
        avg_duration_s=round(avg_duration, 3),
        by_category=by_category,
        failures=[r for r in results if not r.passed],
    )


def result_from_a06(case: EvalCase, a06: dict[str, Any], duration_s: float, error: str = "") -> CaseResult:
    """从 A06 响应构造 CaseResult（运行器适配层）。"""
    used = [t.get("tool", "") for t in (a06.get("used_tools") or [])]
    return CaseResult(
        case_id=case.id,
        category=case.category,
        answer=str(a06.get("answer") or ""),
        used_tools=[t for t in used if t],
        compliance_status=a06.get("compliance_status"),
        need_human_intervention=bool(a06.get("need_human_intervention")),
        duration_s=round(duration_s, 3),
        error=error,
    )
