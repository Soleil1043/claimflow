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
    # 检索命中统计（T033 对比口径：向量条数 / 图谱事实条数，来自 rag_context）
    vector_hits: int = 0
    graph_hits: int = 0
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
    # 工具判分分子/分母（T040：A/B 组间 z 检验需要，率不足以复原样本量）
    tool_scored_passed: int = Field(default=0, description="工具考核通过数")
    tool_scored_total: int = Field(default=0, description="工具考核用例数")
    compliance_pass_rate: float = Field(description="合规通过率：PASS verdict 占比")
    human_precision: float = Field(description="转人工准确率：期望转人工用例中命中占比")
    avg_duration_s: float = Field(description="平均单用例耗时（秒）")
    avg_vector_hits: float = Field(default=0.0, description="平均向量检索命中条数/用例")
    avg_graph_hits: float = Field(default=0.0, description="平均图谱事实命中条数/用例")
    graph_coverage: float = Field(default=0.0, description="图谱命中用例占比（graph_hits>0）")
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
    avg_vector_hits = (sum(r.vector_hits for r in results) / total) if total else 0.0
    avg_graph_hits = (sum(r.graph_hits for r in results) / total) if total else 0.0
    graph_coverage = (sum(1 for r in results if r.graph_hits > 0) / total) if total else 0.0

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
        tool_scored_passed=sum(1 for r in tool_scored if r.tool_match),
        tool_scored_total=len(tool_scored),
        compliance_pass_rate=compliance_pass_rate,
        human_precision=human_precision,
        avg_duration_s=round(avg_duration, 3),
        avg_vector_hits=round(avg_vector_hits, 2),
        avg_graph_hits=round(avg_graph_hits, 2),
        graph_coverage=round(graph_coverage, 4),
        by_category=by_category,
        failures=[r for r in results if not r.passed],
    )


def result_from_a06(
    case: EvalCase, a06: dict[str, Any], duration_s: float, error: str = ""
) -> CaseResult:
    """从 A06 响应构造 CaseResult（运行器适配层）。"""
    used = [t.get("tool", "") for t in (a06.get("used_tools") or [])]

    # 检索命中统计（T033）：rag_node 路径从 shared_data.rag_context 提取；
    # Worker 路径（claim_rule_rag 工具）从 tool_trace 的 results/graph_facts 提取
    vector_hits = 0
    graph_hits = 0
    if isinstance(a06.get("shared_data"), dict):
        rag_ctx = a06["shared_data"].get("rag_context") or {}
        vector_hits = len(rag_ctx.get("results") or [])
        graph_hits = len((rag_ctx.get("graph_facts") or {}).get("facts") or [])
    if vector_hits == 0 and graph_hits == 0:
        for trace in a06.get("used_tools") or []:
            if not isinstance(trace, dict) or trace.get("tool") != "claim_rule_rag":
                continue
            # tool_trace 元素：{agent, tool, input, output}；output 为 ToolOutput dump
            output = trace.get("output") or {}
            data = output.get("data") if isinstance(output, dict) else {}
            if not isinstance(data, dict):
                data = {}
            vector_hits += len(data.get("results") or [])
            graph_hits += len(data.get("graph_facts") or [])

    return CaseResult(
        case_id=case.id,
        category=case.category,
        answer=str(a06.get("answer") or ""),
        used_tools=[t for t in used if t],
        compliance_status=a06.get("compliance_status"),
        need_human_intervention=bool(a06.get("need_human_intervention")),
        duration_s=round(duration_s, 3),
        error=error,
        vector_hits=vector_hits,
        graph_hits=graph_hits,
    )


# ===== A/B 组间对比（T040） =====


def two_proportion_z_test(
    passed_a: int, total_a: int, passed_b: int, total_b: int
) -> dict[str, Any]:
    """双比例 z 检验（显著性粗判）：两组成功率的差异是否显著（|z| > 1.96 ≈ p < 0.05）。

    池化比例口径：z = (p_a - p_b) / sqrt(p_pool(1-p_pool)(1/n_a + 1/n_b))。
    任一组样本为 0 时返回 z=0 不显著（保守）。
    """
    if total_a <= 0 or total_b <= 0:
        return {"z": 0.0, "significant_p05": False, "note": "样本量为 0"}
    p_a = passed_a / total_a
    p_b = passed_b / total_b
    p_pool = (passed_a + passed_b) / (total_a + total_b)
    se = (p_pool * (1 - p_pool) * (1 / total_a + 1 / total_b)) ** 0.5
    if se == 0:
        # 两组全对或全错（比例无差异）
        return {"z": 0.0, "significant_p05": False, "note": "池化方差为 0（两组比例相同）"}
    z = (p_a - p_b) / se
    return {"z": round(z, 3), "significant_p05": abs(z) > 1.96}
