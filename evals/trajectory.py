"""轨迹质量判分（T050，D026 独立报告口径——不并入 passed）。

与 metrics.py 的五维答案判分平行：只看"怎么调的工具"。五个维度：
- 顺序：期望序列须为实际序列的按序子序列（LCS 占比 == 1.0），容忍合理插入、惩罚乱序
- 禁调：forbidden_tools 任一出现在实际轨迹即违规
- 路由：期望 Agent 序列 vs 实际序列（task_plan 派生）做按序子序列匹配
- 次数：调用总数 ≤ max_tool_calls；冗余计数 = (tool, input) 完全相同的重复调用次数
- 入参：expected_tool_args 按工具名做关键入参子集断言（防参数幻觉）

未标注维度不写 trajectory_expectations、一律视为通过（与 expected_tools 为空不考核
的既有原则一致），聚合时分母只计标注用例。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from evals.schemas import EvalCase

if TYPE_CHECKING:
    from evals.metrics import CaseResult


def lcs_length(a: Sequence[str], b: Sequence[str]) -> int:
    """最长公共子序列长度（滚动行，O(len(a)*len(b)) 时间 / O(min) 空间）。"""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            if x == y:
                cur.append(prev[j - 1] + 1)
            else:
                cur.append(max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def count_redundant(trace: list[dict[str, Any]]) -> int:
    """冗余调用计数：(tool, input) 完全相同的重复调用，每次多出记一次。"""
    seen: dict[str, int] = {}
    for t in trace:
        key = (
            str(t.get("tool", ""))
            + "|"
            + json.dumps(t.get("input") or {}, ensure_ascii=False, sort_keys=True, default=str)
        )
        seen[key] = seen.get(key, 0) + 1
    return sum(c - 1 for c in seen.values() if c > 1)


def _args_match(expected: dict[str, dict[str, Any]], trace: list[dict[str, Any]]) -> bool:
    """入参子集断言：每个工具的期望键值对，在实际该工具任一次调用入参中全部命中。"""
    from evals.metrics import _norm  # 局部导入避免与 metrics 顶层互相引用

    by_tool: dict[str, list[dict[str, Any]]] = {}
    for t in trace:
        input_ = t.get("input")
        if isinstance(input_, dict):
            by_tool.setdefault(str(t.get("tool", "")), []).append(input_)

    for tool, want in expected.items():
        candidates = by_tool.get(tool, [])
        ok = any(
            all(
                key in c
                and str(c[key]).strip() != ""
                and _norm(str(c[key])) == _norm(str(want_value))
                for key, want_value in want.items()
            )
            for c in candidates
        )
        if not ok:
            return False
    return True


def score_trajectory(case: EvalCase, result: CaseResult) -> CaseResult:
    """按轨迹期望判分，写回各分项与 trajectory_expectations（不触碰 passed）。

    trajectory_expectations 记录该用例实际考核的维度（聚合分母只计这些用例）。
    """
    tools_seq = [str(t.get("tool", "")) for t in result.tool_trace]

    if case.expected_tool_order:
        result.trajectory_expectations["order"] = True
        hits = lcs_length(case.expected_tool_order, tools_seq)
        result.order_ratio = round(hits / len(case.expected_tool_order), 3)
        result.order_match = hits == len(case.expected_tool_order)

    if case.forbidden_tools:
        result.trajectory_expectations["forbidden"] = True
        result.forbidden_clean = not (set(case.forbidden_tools) & set(tools_seq))

    if case.expected_route:
        result.trajectory_expectations["route"] = True
        hits = lcs_length(case.expected_route, result.agent_route)
        result.route_match = hits == len(case.expected_route)

    if case.max_tool_calls is not None:
        result.trajectory_expectations["limit"] = True
        result.calls_within_limit = len(result.tool_trace) <= case.max_tool_calls

    if case.expected_tool_args:
        result.trajectory_expectations["args"] = True
        result.args_match = _args_match(case.expected_tool_args, result.tool_trace)

    result.redundant_calls = count_redundant(result.tool_trace)
    return result
