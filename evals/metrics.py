"""评测指标计算（T027，architecture.md 9.1）。

纯函数层：单用例判分（answer/tool_trace/compliance → CaseResult）+ 数据集聚合
（任务完成率/工具调用准确率/合规通过率/转人工准确率/平均耗时）。
运行器（test_suite.py）只负责调度与 IO，判分逻辑全部在此（可单测）。
"""

from __future__ import annotations

import math
import re
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
    # 轨迹（T050，D026）：按序摘要 {agent, tool, input}（output 不入库控报告体积）
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    agent_route: list[str] = Field(
        default_factory=list, description="实际 Agent 路由序列（task_plan 派生，去重保序）"
    )
    # 轨迹判分明细（独立口径，不并入 passed；未标注维度保持默认通过）
    trajectory_expectations: dict[str, bool] = Field(
        default_factory=dict, description="该用例实际考核的轨迹维度（聚合分母只计这些用例）"
    )
    order_ratio: float = 1.0
    order_match: bool = True
    forbidden_clean: bool = True
    route_match: bool = True
    calls_within_limit: bool = True
    args_match: bool = True
    redundant_calls: int = 0
    # 判分明细
    tool_match: bool = True  # 期望工具为空时视为通过（该用例不考核工具）
    must_include_hit: bool = True
    any_of_hit: bool = True
    must_not_include_clean: bool = True
    human_match: bool = True
    # 数值精确断言（T067）：expected_numbers 为空不考核
    numbers_hit: bool = True
    # 转人工指标（T064，BUG-001）：透传用例期望值，聚合 precision/recall 才有正确分母分子
    expect_human: bool = False
    # 意图准确率（T066，BUG-002）：actual_intent 回填实际识别结果；
    # intent_match=None 表示该用例未标注期望意图（不考核，聚合分母不计）
    actual_intent: str | None = None
    intent_match: bool | None = None
    # LLM-judge 结果（T068，独立口径不并入 passed）：
    # {faithfulness, completeness, compliance, total, pass, rationale} 或 None（未 judge/失败）
    judge: dict[str, Any] | None = None
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
    avg_duration_s: float = Field(description="平均单用例耗时（秒）")
    p95_duration_s: float = Field(
        default=0.0, description="95 分位耗时（秒，最近秩法）——均值会掩盖长尾（T073，GAP-006）"
    )
    tokens_per_case: float | None = Field(
        default=None,
        description="单用例平均 LLM token 消耗（Prometheus 差分注入；None=运行器未提供，T073）",
    )
    wilson_ci: list[float] = Field(
        default_factory=list,
        description="任务完成率 Wilson 95% 置信区间 [lo, hi]（GAP-008：n=200 时半宽约 ±4.5pp）",
    )
    human_precision: float = Field(
        description="转人工精确率：实际转人工的用例中「确实该转」（用例标注期望）的占比"
    )
    human_recall: float = Field(
        description="转人工召回率：期望转人工的用例中实际转了人工的占比（北极星口径）"
    )
    human_scored: int = Field(
        default=0, description="期望转人工用例数（recall 分母；0 表示本轮无该类标注）"
    )
    human_intervened: int = Field(
        default=0, description="实际转人工用例数（precision 分母）"
    )
    intent_accuracy: float | None = Field(
        default=None,
        description="意图准确率：期望意图用例中识别一致的占比（None=本轮无标注，未考核）",
    )
    intent_scored: int = Field(
        default=0, description="意图考核用例数（分母；expected_intent 标注数）"
    )
    # LLM-judge 独立口径（T068，D033 同 D026：不并入 passed，先观察后收敛）
    judge_pass_rate: float | None = Field(
        default=None,
        description="judge 判过率：must_include 为空用例中 rubric 总分 ≥4 占比（None=未启用/无产出）",
    )
    judge_scored: int = Field(default=0, description="judge 成功产出判分的用例数")
    avg_duration_s: float = Field(description="平均单用例耗时（秒）")
    avg_vector_hits: float = Field(default=0.0, description="平均向量检索命中条数/用例")
    avg_graph_hits: float = Field(default=0.0, description="平均图谱事实命中条数/用例")
    graph_coverage: float = Field(default=0.0, description="图谱命中用例占比（graph_hits>0）")
    by_category: dict[str, dict[str, float]] = Field(default_factory=dict)
    # 轨迹质量指标（T050，D026 独立口径）：{维度: {rate, scored}}，scored=标注用例数
    # （rate 在 scored=0 时为 1.0，表示"无标注不考核"，与 tool_accuracy 空分母口径一致）
    trajectory: dict[str, dict[str, float]] = Field(default_factory=dict)
    failures: list[CaseResult] = Field(default_factory=list, description="失败用例明细")


def _norm(s: str) -> str:
    """判分用归一化：去空白与千分位逗号（'30 天'→'30天'、'4,640'→'4640'、'10,000'→'10000'）。"""
    return s.replace(" ", "").replace("\u3000", "").replace(",", "")


# 数值断言归一化（T067）：全角数字/小数点/逗号/百分号 → 半角
_FULLWIDTH_MAP = str.maketrans("０１２３４５６７８９．，％", "0123456789.,%")


def _norm_number(s: str) -> str:
    """数值断言归一化：全角→半角、去千分位逗号与空白、纯零小数尾折叠（'4640.00'→'4640'）。

    '4640.50' 保留不动（有效金额，不是尾零）。
    """
    s = s.translate(_FULLWIDTH_MAP).replace(",", "").replace(" ", "").replace("\u3000", "")
    # 数字后跟 ".000…"（小数部分全零且后继不是数字）→ 折叠为整数
    return re.sub(r"(\d)\.0+(?!\d)", r"\1", s)


def number_hit(answer: str, expected: str) -> bool:
    """精确数值断言：归一化后按数字边界匹配（'640' 不许混过 '4640'，'.5' 不许粘连）。"""
    target = _norm_number(expected)
    if not target:
        return True
    return re.search(rf"(?<![\d.]){re.escape(target)}(?![\d.])", _norm_number(answer)) is not None


def score_case(case: EvalCase, result: CaseResult) -> CaseResult:
    """按用例要点判分，写回各分项与 passed。

    - tool_match：期望工具集合 ⊆ 实际调用集合（子集匹配；期望为空不考核）
    - must_include：全部命中（归一化子串）
    - any_of：任一命中（无 any_of 要求时视为通过）
    - must_not_include：全部未出现
    - expected_numbers：全部数值精确命中（数字归一化 + 边界断言，T067）
    - human_match：期望转人工 ↔ 实际 need_human_intervention 一致
    - intent_match：期望意图 ↔ 实际识别意图一致（未标注 → None 不考核，T066）
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

    if case.expected_numbers:
        result.numbers_hit = all(
            number_hit(result.answer, n) for n in case.expected_numbers
        )

    result.expect_human = case.expect_human_intervention
    result.human_match = case.expect_human_intervention == result.need_human_intervention

    # 意图比对（T066，BUG-002）：未标注期望意图的用例 intent_match 保持 None（不考核）
    if case.expected_intent:
        result.intent_match = result.actual_intent == case.expected_intent

    result.passed = (
        result.tool_match
        and result.must_include_hit
        and result.any_of_hit
        and result.must_not_include_clean
        and result.numbers_hit
        and result.human_match
        and not result.error
    )
    # 轨迹判分（T050，D026）：只写分项与 trajectory_expectations，不参与 passed
    from evals.trajectory import score_trajectory  # 局部导入避免顶层循环引用

    score_trajectory(case, result)
    return result


def aggregate(
    results: list[CaseResult], *, tokens_total: int | None = None
) -> EvalReport:
    """聚合为评测报告。

    tokens_total：本轮运行 LLM token 总消耗（运行器 Prometheus 差分口径注入，T073）；
    None 表示未提供（报告 tokens_per_case=None 不误导）。
    """
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

    # 转人工指标（T064，BUG-001 修复）：precision 与 recall 分子分母各归其位——
    # precision：实际转人工中「确实该转」（用例标注期望）的占比，考察"别乱转"；
    # recall：期望转人工中被转了的占比，考察"该转的别漏"，北极星（62%→37%）的主口径
    intervened = [r for r in results if r.need_human_intervention]
    expected_true = [r for r in results if r.expect_human]
    human_precision = (
        sum(1 for r in intervened if r.expect_human) / len(intervened) if intervened else 0.0
    )
    human_recall = (
        sum(1 for r in expected_true if r.need_human_intervention) / len(expected_true)
        if expected_true
        else 0.0
    )

    # 意图准确率（T066，BUG-002）：分母只计标注了 expected_intent 的用例；
    # 空分母输出 None（未考核）而非 1.0，避免"无数据=满分"误读（D033）
    intent_scored = [r for r in results if r.intent_match is not None]
    intent_accuracy = (
        sum(1 for r in intent_scored if r.intent_match) / len(intent_scored)
        if intent_scored
        else None
    )

    # LLM-judge 聚合（T068，独立口径）：分母只计成功产出 judge 记录的用例
    judged = [r for r in results if r.judge]
    judge_pass_rate = (
        sum(1 for r in judged if r.judge.get("pass")) / len(judged) if judged else None
    )

    avg_duration = (sum(r.duration_s for r in results) / total) if total else 0.0
    # p95 耗时（最近秩法）：均值会骗人——一人火锅一人冰"平均体温"都正常（T073）
    durations = sorted(r.duration_s for r in results)
    p95_idx = max(0, math.ceil(0.95 * len(durations)) - 1) if durations else 0
    p95_duration = durations[p95_idx] if durations else 0.0
    tokens_per_case = (tokens_total / total) if (tokens_total is not None and total) else None
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

    # 轨迹质量聚合（T050，D026 独立口径）：分母只计标注了对应维度的用例
    def _traj(key: str, attr: str) -> dict[str, float]:
        scored = [r for r in results if r.trajectory_expectations.get(key)]
        rate = sum(1 for r in scored if getattr(r, attr)) / len(scored) if scored else 1.0
        return {"rate": round(rate, 4), "scored": float(len(scored))}

    with_trace = [r for r in results if r.tool_trace]
    trajectory = {
        "order": _traj("order", "order_match"),
        "route": _traj("route", "route_match"),
        "forbidden": _traj("forbidden", "forbidden_clean"),
        "args": _traj("args", "args_match"),
        "limit": _traj("limit", "calls_within_limit"),
        "redundancy": {
            "avg_redundant_calls": round(
                sum(r.redundant_calls for r in with_trace) / len(with_trace) if with_trace else 0.0,
                2,
            ),
            "cases_with_trace": float(len(with_trace)),
        },
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
        human_recall=human_recall,
        human_scored=len(expected_true),
        human_intervened=len(intervened),
        avg_duration_s=round(avg_duration, 3),
        p95_duration_s=round(p95_duration, 3),
        tokens_per_case=round(tokens_per_case, 1) if tokens_per_case is not None else None,
        wilson_ci=wilson_ci(passed, total),
        intent_accuracy=intent_accuracy,
        intent_scored=len(intent_scored),
        judge_pass_rate=judge_pass_rate,
        judge_scored=len(judged),
        avg_vector_hits=round(avg_vector_hits, 2),
        avg_graph_hits=round(avg_graph_hits, 2),
        graph_coverage=round(graph_coverage, 4),
        by_category=by_category,
        trajectory=trajectory,
        failures=[r for r in results if not r.passed],
    )


def result_from_a06(
    case: EvalCase, a06: dict[str, Any], duration_s: float, error: str = ""
) -> CaseResult:
    """从 A06 响应构造 CaseResult（运行器适配层）。"""
    # 轨迹来源优先 tool_trace（T050 完整 {agent,tool,input,output}），旧键 used_tools 兜底
    trace = [
        t for t in (a06.get("tool_trace") or a06.get("used_tools") or []) if isinstance(t, dict)
    ]
    used = [t.get("tool", "") for t in trace]

    # 检索命中统计（T033）：rag_node 路径从 shared_data.rag_context 提取；
    # Worker 路径（claim_rule_rag 工具）从轨迹的 results/graph_facts 提取
    vector_hits = 0
    graph_hits = 0
    if isinstance(a06.get("shared_data"), dict):
        rag_ctx = a06["shared_data"].get("rag_context") or {}
        vector_hits = len(rag_ctx.get("results") or [])
        graph_hits = len((rag_ctx.get("graph_facts") or {}).get("facts") or [])
    if vector_hits == 0 and graph_hits == 0:
        for trace_item in trace:
            if trace_item.get("tool") != "claim_rule_rag":
                continue
            # 轨迹元素：{agent, tool, input, output}；output 为 ToolOutput dump
            output = trace_item.get("output") or {}
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
        # 意图（T066）：state.intent 为 intent 节点识别结果（StrEnum 值转 str）
        actual_intent=str(a06["intent"]) if a06.get("intent") else None,
        vector_hits=vector_hits,
        graph_hits=graph_hits,
        # 轨迹摘要（D026）：input 保留用于入参断言，output 不入库控报告体积
        tool_trace=[
            {
                "agent": str(t.get("agent") or ""),
                "tool": str(t.get("tool") or ""),
                "input": t.get("input") or {},
            }
            for t in trace
        ],
        agent_route=[str(a) for a in (a06.get("agent_route") or [])],
    )


# ===== A/B 组间对比（T040） =====


def wilson_ci(passed: int, total: int, z: float = 1.96) -> list[float]:
    """Wilson 95% 置信区间（T072/T073，GAP-008）：小样本率的可信区间。

    比 Wald 正态近似稳健（n=200、p=88% 时半宽约 ±4.5pp）——
    88.0% vs 87.0% 这类差异是否在噪声内由区间直接可判。
    total<=0 时返回 [0, 1]。
    """
    if total <= 0:
        return [0.0, 1.0]
    p = passed / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    half = (z * (p * (1 - p) / total + z**2 / (4 * total**2)) ** 0.5) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


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
