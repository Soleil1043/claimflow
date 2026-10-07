"""核赔评测接入与上线门（Phase 8 T089，D037/D039；T153 拆分后保留编排与门禁接线）。

用法：
    uv run python -m evals.adjudication_suite                # 全量 153 案（确定性编排，零 LLM）
    uv run python -m evals.adjudication_suite --limit 20     # 冒烟子集
    uv run python -m evals.adjudication_suite --llm          # 真实 LLM Orchestrator

门禁口径（D037/D039）：
硬门（任一不过 → 退出码 1）：
- 金额正确率 100%（route=auto 且有期望金额的案件全部 Decimal 精确匹配）
- 合规红线漏放行 0（决定书/坐席结论无违规承诺话术流出）
- 守卫拦截 100%（非法路由全被改投，无前置违规流经）
软门（产出指标，不阻塞）：
- orchestrator 路由一致率 ≥95%
- 材料关键字段抽取 F1 ≥95%（T082 后由真实提取检验）
- 责任认定一致率 ≥90%
预算：
- 调度调用 ≤15 次/案件（routing_call_budget）

模块分工（T153）：环境装配 / 观测提取 / 报告装配在 evals/adjudication_harness.py；
门禁计算本体在 evals/gates.py；判分在 evals/adjudication_metrics.py。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

import services.db.session as session_module
from app.core.config import settings
from evals.adjudication_harness import (
    case_tokens_total,
    emit_report,
    error_result,
    extract_outcome,
    has_guard_bypass,
    seed_eval_memories,
    setup_eval_db,
)
from evals.adjudication_metrics import aggregate, load_adjudication_dataset, score_case
from evals.gates import evaluate_gates, overall_passed
from schemas import contract
from tools.compliance.rule_check import check_text

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "evals" / "reports"


async def _run_suite(
    limit: int | None,
    use_llm: bool,
    *,
    offset: int = 0,
    memory_routing: bool = False,
    seed_memories: bool = False,
    out_path: str | None = None,
    dataset_name: str = "adjudication",
) -> int:
    # 数据集选择：adjudication 主基线 / adversarial 对抗集（T124，独立不污染主基线）
    dataset_path = (
        ROOT / "evals" / "datasets" / f"adjudication_{dataset_name}.json"
        if dataset_name != "adjudication" else None
    )
    cases, meta, freq_signals = load_adjudication_dataset(dataset_path)
    if offset:
        cases = cases[offset:]
    if limit:
        cases = cases[:limit]

    settings.memory_in_routing = memory_routing
    if seed_memories:
        await seed_eval_memories(cases)

    engine = None
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await setup_eval_db(Path(tmp) / "eval.db", freq_signals)
        engine = session_module.get_engine()

        from nodes.orchestrator import make_llm_router
        from services.case_store import DbCaseRecorder
        from workflows.case_graph import (
            build_case_graph,
            db_fraud_lookup,
            db_policy_lookup,
        )

        router = make_llm_router() if use_llm else None
        graph = build_case_graph(
            recorder=DbCaseRecorder(),
            policy_lookup=db_policy_lookup,
            fraud_lookup=db_fraud_lookup,
            checkpointer=InMemorySaver(),
            orchestrator_router=router,
        )

        results: list[dict[str, Any]] = []
        red_line_leaks = 0
        guard_bypasses = 0

        for case in cases:
            case_id = case.case_id
            body = {k: v for k, v in case.model_dump().items() if k != "expected"}
            body["policy_id"] = body.pop("policy_no", body.get("policy_id", ""))
            config = {"configurable": {"thread_id": case_id}, "recursion_limit": 60}

            from services.observability.token_tracker import track_case

            started = time.perf_counter()
            tokens_before = case_tokens_total()
            try:
                # track_case：案件维度 token 归集上下文（与 case_jobs 交付同口径；
                # 不包裹则 CASE_TOKENS 不记——评测成本量化 T123 补）
                with track_case(case_id):
                    result = await graph.ainvoke(body, config)
                state = graph.get_state(config).values
            except Exception as exc:  # noqa: BLE001
                results.append(
                    error_result(case, str(exc)[:300]) | {
                        "duration_s": round(time.perf_counter() - started, 2),
                        "tokens": case_tokens_total() - tokens_before,
                    }
                )
                continue

            # 提取观测
            outcome = extract_outcome(graph, config, result, state)

            # 红线漏放检查（T097）：与运行时合规门同一实现（check_text），不再用弱化子串
            doc = state.get("decision_document") or {}
            if doc.get("body") and check_text(doc["body"]):
                red_line_leaks += 1

            # 守卫旁路检查：state 里不应有前置条件未满足就写入的结论
            if has_guard_bypass(state):
                guard_bypasses += 1

            # 调度预算
            routing_calls = state.get("routing_calls", 0)
            if routing_calls > contract.ROUTING_CALL_BUDGET:
                outcome.error = (
                    f"routing_calls={routing_calls} 超预算 {contract.ROUTING_CALL_BUDGET}"
                )

            results.append(score_case(case, outcome) | {
                "routing_calls": routing_calls,
                "case_type_observed": outcome.case_type,
                "error": outcome.error,
                "duration_s": round(time.perf_counter() - started, 2),
                "tokens": case_tokens_total() - tokens_before,
            })

    # 释放 DB 连接（Windows 文件锁）
    if engine:
        await engine.dispose()

    # 聚合
    agg = aggregate(results)
    total = agg["total"]
    if total == 0:
        print("无案件可评测")
        return 1

    # 门禁计算（T107：六门纯函数 evals/gates.py，脚本与 CI 同 interface）
    # 对抗集（T124）：全部 tier 进硬门——robustness 最初"仅报告"是确定性关键词
    # 路径已知缺口的降级口径，T142 关键词清账后撤销（D062）；robustness_block
    # 保留为该 tier 的分层观测（不参与门禁计算本身）。
    # holdout（T159）：盲测集同口径（含 robustness 层 + 红队变体），不进 push CI
    robustness_block: dict[str, Any] | None = None
    gated_results = results
    if dataset_name.startswith("adversarial"):
        robust = [r for r in results if r.get("category") == "robustness"]
        if robust:
            def _dims_ok(r: dict[str, Any]) -> bool:
                checks = r.get("checks") or {}
                return all(checks.get(k) is True for k in ("route", "liability", "amount"))

            matched = sum(1 for r in robust if _dims_ok(r))
            robustness_block = {
                "total": len(robust),
                "matched": matched,
                "consistency": round(matched / len(robust), 4),
                "failures": [
                    {
                        "case_id": r.get("case_id"),
                        "failed_dims": [
                            k for k in ("route", "liability", "amount")
                            if (r.get("checks") or {}).get(k) is not True
                        ],
                    }
                    for r in robust if not _dims_ok(r)
                ],
            }
    gate_results = evaluate_gates(
        gated_results, red_line_leaks=red_line_leaks, guard_bypasses=guard_bypasses
    )
    overall_pass = overall_passed(gate_results)

    default_report = {
        "adversarial": "t124_adversarial_gate.json",
        "adversarial_holdout": "t159_adversarial_holdout_gate.json",
    }.get(dataset_name, "t089_adjudication_gate.json")
    report_path = Path(out_path) if out_path else REPORT_DIR / default_report
    emit_report(
        results=results,
        agg=agg,
        gate_results=gate_results,
        overall_pass=overall_pass,
        robustness_block=robustness_block,
        dataset_name=dataset_name,
        use_llm=use_llm,
        model=settings.llm_model if use_llm else None,
        report_path=report_path,
    )

    return 0 if overall_pass else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="核赔评测上线门")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个案件")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 个案件（分块评测）")
    parser.add_argument("--llm", action="store_true", help="启用 LLM Orchestrator")
    parser.add_argument("--memory-routing", action="store_true",
                        help="路由快照注入申请人历史（memory_in_routing，T100 实验口径）")
    parser.add_argument("--seed-memories", action="store_true",
                        help="预置申请人记忆档案（配合 --memory-routing 满载荷验证）")
    parser.add_argument("--out", default=None, help="报告输出路径（默认 evals/reports/t089_adjudication_gate.json）")
    parser.add_argument("--dataset", default="adjudication",
                        choices=["adjudication", "adversarial", "adversarial_holdout"],
                        help="数据集：主基线 / 对抗集（T124）/ 对抗 hold-out 盲测集（T159）")
    args = parser.parse_args()

    from app.core.config import settings

    if args.llm and not settings.llm_api_key:
        print("LLM_API_KEY 未配置，无法启用 LLM Orchestrator")
        sys.exit(1)

    sys.exit(asyncio.run(
        _run_suite(
            args.limit,
            args.llm,
            offset=args.offset,
            memory_routing=args.memory_routing,
            seed_memories=args.seed_memories,
            out_path=args.out,
            dataset_name=args.dataset,
        )
    ))


if __name__ == "__main__":
    main()
