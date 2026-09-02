"""T069 半自动轨迹标注：以真实运行轨迹为参考，为 multi_step 生成 order/route 标注草案。

流程（审计 P1-3 建议口径）：
1. 读 scripts/collect_traces.py 的参考轨迹（evals/reports/t069_trace_reference.json）
2. 规则生成标注草案：
   - order：按业务依赖排序 expected_tools（查询 → 医疗 → 规则 → 计算）
   - route：工具 → Agent 映射去重保序（claim_rule_rag 跟随同单其他工具的 Agent；
     仅含 rag 工具时不标 route——claim/medical 均可承载，二义不标）
3. 与实际轨迹对照：标注草案 vs 实际序列做按序子序列检查，不一致的标 ⚠ 供人工裁决
4. 无 expected_tools 的用例：参考实际轨迹 + 语义关键词给建议（默认不自动标 order，
   只在人工 --apply-observed 时采纳实际高频序列）

用法：
    uv run python scripts/annotate_trajectory.py                # 打印草案与差异
    uv run python scripts/annotate_trajectory.py --apply        # 写盘（仅规则可判定项）
    uv run python scripts/annotate_trajectory.py --apply-observed  # 含采纳实际轨迹的二义项
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATASET = Path("evals/datasets/eval_dataset.json")
REFERENCE = Path("evals/reports/t069_trace_reference.json")

# 工具业务序（小者先）：查询 → 医疗取证 → 规则 → 计算/出结果
_TOOL_ORDER_RANK = {
    "policy_query": 0,
    "claim_status_query": 0,
    "record_query": 1,
    "ocr_extract": 1,
    "diagnosis_matcher": 2,
    "claim_rule_rag": 3,
    "claim_calculator": 4,
}
# 工具 → Agent（agents/claim.py、agents/medical.py 的 tool_names）
_TOOL_AGENT = {
    "policy_query": "claim",
    "claim_calculator": "claim",
    "claim_status_query": "claim",
    "record_query": "medical",
    "diagnosis_matcher": "medical",
    "ocr_extract": "medical",
    # claim_rule_rag 两 Agent 均持有：跟随其他工具，单独出现时不定
}


def derive_order(expected_tools: list[str]) -> list[str]:
    """期望工具集合 → 按业务依赖排序的调用序列。"""
    return sorted(expected_tools, key=lambda t: _TOOL_ORDER_RANK.get(t, 3))


def derive_route(order: list[str]) -> list[str]:
    """工具序列 → Agent 路由序列（去重保序；纯 rag 序列返回空=二义不标）。"""
    if not order:
        return []
    fixed = [t for t in order if t in _TOOL_AGENT]
    if not fixed:  # 仅 claim_rule_rag
        return []
    route: list[str] = []
    for t in fixed:
        agent = _TOOL_AGENT[t]
        if agent not in route:
            route.append(agent)
    return route


# 语义手工期望（无 expected_tools 依据，按业务语义定；实际轨迹供对照不固化波动）：
# MS-023 无保单号：核对保障范围+算赔付 → 医疗取证 + 规则（无从查询/计算具体金额）
# MS-058 有保单号的慢性病投保前史 → 医疗取证 + 查保单 + 核算
# MS-059 投保半月查出肾结石（等待期判断）→ 查保单 + 规则
# MS-069 意外/车险责任咨询 → 规则
_SPECIAL_EXPECTATIONS: dict[str, dict] = {
    "MS-023": {"expected_tools": ["diagnosis_matcher", "claim_rule_rag"]},
    "MS-058": {
        "expected_tools": ["record_query", "diagnosis_matcher", "policy_query", "claim_calculator"]
    },
    "MS-059": {"expected_tools": ["policy_query", "claim_rule_rag"]},
    "MS-069": {"expected_tools": ["claim_rule_rag"]},
}


def is_subsequence(expect: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(x in it for x in expect)


def main() -> None:
    apply = "--apply" in sys.argv
    apply_observed = "--apply-observed" in sys.argv

    ref = {r["id"]: r for r in json.loads(REFERENCE.read_text(encoding="utf-8"))}
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    ms = [c for c in data["cases"] if c["category"] == "multi_step"]

    n_annotated = n_observed = n_skip = n_conflict = 0
    for case in ms:
        cid = case["id"]
        if case.get("expected_tool_order") and case.get("expected_route"):
            n_skip += 1
            continue
        observed = ref.get(cid) or {}

        # 语义手工期望优先（无 expected_tools 的用例，按业务语义定标注）
        special = _SPECIAL_EXPECTATIONS.get(cid)
        if special and not case.get("expected_tools"):
            case["expected_tools"] = special["expected_tools"]

        if case.get("expected_tools"):
            # 规则可判定：order/route 从既有 expected_tools 派生
            order = derive_order(case["expected_tools"])
            route = derive_route(order)
            case["expected_tool_order"] = order
            # route 维度前提是系统走 supervisor 规划（task_plan 非空）——
            # 实际 route 为空（单领域直连/纯 RAG 路径）时该维度无从考核，不标
            if observed.get("route"):
                case["expected_route"] = route
            n_annotated += 1
            ok_order = is_subsequence(order, observed.get("tools") or [])
            ok_route = (not route) or is_subsequence(route, observed.get("route") or [])
            flag = "" if (ok_order and ok_route) else " ⚠️ 与实际不一致"
            if flag:
                n_conflict += 1
            print(
                f"{cid}: order={order} route={case.get('expected_route', [])}"
                f" | 实际 tools={observed.get('tools')} route={observed.get('route')}{flag}"
            )
        elif apply_observed and observed.get("tools") == ["claim_rule_rag"]:
            # 采纳实际仅限稳定单工具形态（纯 RAG 咨询 27 条）；
            # 多工具/病态/空轨迹不固化（波动本身是 D026 要观察的方差，不是标注依据）
            case["expected_tools"] = ["claim_rule_rag"]
            case["expected_tool_order"] = ["claim_rule_rag"]
            # route 不标：纯 RAG 路径 task_plan 为空
            n_observed += 1
            print(f"{cid}: 采纳稳定形态 order=['claim_rule_rag']（route 不标）")
        else:
            print(
                f"{cid}: 无 expected_tools，未标注（实际 tools={observed.get('tools')}"
                f" route={observed.get('route')}，形态不稳定不固化）"
            )

    print(
        f"\n规则标注 {n_annotated} 条 | 采纳实际 {n_observed} 条 | 已有跳过 {n_skip} 条"
        f" | 与实际冲突 {n_conflict} 条"
    )
    if apply or apply_observed:
        DATASET.write_text(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print("已写盘")
    else:
        print("(dry-run，未写盘；--apply 写规则项 / --apply-observed 含采纳实际轨迹)")


if __name__ == "__main__":
    main()
