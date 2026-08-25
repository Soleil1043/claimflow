"""评测运行器（T027）。

用法：
    uv run python -m evals.test_suite                        # 全量 200 条
    uv run python -m evals.test_suite --category simple_faq  # 按分类子集
    uv run python -m evals.test_suite --limit 10             # 前 N 条
    uv run python -m evals.test_suite --out my_report.json   # 指定输出路径

流程：构建主图（真实 LLM + Mock 工具）→ 逐条 ainvoke → metrics 判分 → 聚合 → JSON 落盘。
基线报告：evals/reports/baseline.json（T027 验收产出，后续回归对比用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.core.logging import configure_logging, get_logger
from evals.metrics import CaseResult, aggregate, result_from_a06, score_case
from evals.schemas import EvalCase, EvalCategory, EvalDataset

log = get_logger(__name__)

DATASET_PATH = Path("evals/datasets/eval_dataset.json")
REPORTS_DIR = Path("evals/reports")


def load_cases(category: str | None, limit: int | None) -> tuple[list[EvalCase], str]:
    """加载数据集并按参数过滤。"""
    dataset = EvalDataset.model_validate_json(DATASET_PATH.read_text(encoding="utf-8"))
    cases = dataset.cases
    if category:
        cases = [c for c in cases if c.category == category]
    if limit:
        cases = cases[:limit]
    return cases, dataset.version


async def run_case(graph: Any, case: EvalCase) -> CaseResult:
    """执行单条用例：每条独立 thread（避免多轮上下文互相干扰）。"""
    thread_id = f"eval-{case.id}"
    started = time.perf_counter()
    error = ""
    a06: dict[str, Any] = {}
    try:
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=case.user_input)],
                "conversation_id": thread_id,
                "intent": None,
                "task_plan": [],
                "current_step": 0,
                "shared_data": {},
                "agent_steps": [],
                "tool_trace": [],
                "compliance_result": None,
                "compliance_rounds": 0,
                "final_answer": "",
                "need_human_intervention": False,
                "intervention_reason": None,
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        a06 = result
    except Exception as exc:  # noqa: BLE001 单用例失败不中断整轮评测
        error = str(exc)[:200]
        log.warning("eval_case_error", case=case.id, error=error)

    # ainvoke 返回最终 state：final_answer 在合成/合规节点写入
    if a06.get("final_answer") and not a06.get("answer"):
        a06["answer"] = a06["final_answer"]
    if a06.get("compliance_result") and not a06.get("compliance_status"):
        a06["compliance_status"] = (a06["compliance_result"] or {}).get("verdict")
    # 工具轨迹：A06 层由 API 处理器从 tool_trace 提取；评测直调图，这里同样转换
    if not a06.get("used_tools") and a06.get("tool_trace"):
        a06["used_tools"] = a06["tool_trace"]

    cr = result_from_a06(case, a06, time.perf_counter() - started, error)
    # simple_faq 走 rag_node 直检（不经过 claim_rule_rag 工具），轨迹无 tool 记录——
    # 评测口径与 A06 一致：用 shared_data.rag_context 命中补记 used_tools
    if not cr.used_tools and isinstance(a06.get("shared_data"), dict):
        rag_ctx = a06["shared_data"].get("rag_context") or {}
        if rag_ctx.get("results"):
            cr.used_tools = ["claim_rule_rag"]
    return score_case(case, cr)


async def main() -> None:
    parser = argparse.ArgumentParser(description="claimflow 评测运行器")
    parser.add_argument("--category", choices=[c.value for c in EvalCategory], default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=None, help="报告输出路径（默认 evals/reports/<时间戳>.json）")
    args = parser.parse_args()

    configure_logging()

    cases, version = load_cases(args.category, args.limit)
    print(f"加载 {len(cases)} 条用例（dataset v{version}, category={args.category or '全部'}）")

    # 构建主图：真实 LLM（需 .env LLM_API_KEY）+ dev profile 零容器依赖
    import tools.claim  # noqa: F401 注册理赔工具
    import tools.compliance  # noqa: F401 注册合规工具
    import tools.medical  # noqa: F401 注册医疗工具
    from services.memory.short_term import get_checkpoint_manager
    from tools.executor import ToolExecutor
    from tools.registry import get_default_registry
    from workflows.main_graph import build_main_graph

    registry = get_default_registry()
    checkpointer = await get_checkpoint_manager().start()
    graph = build_main_graph(executor=ToolExecutor(registry), checkpointer=checkpointer)

    results: list[CaseResult] = []
    passed_count = 0
    for i, case in enumerate(cases, 1):
        cr = await run_case(graph, case)
        results.append(cr)
        passed_count += cr.passed
        mark = "PASS" if cr.passed else "FAIL"
        print(f"[{i:>3}/{len(cases)}] {mark} {case.id} {case.user_input[:30]}")

    report = aggregate(results)
    await get_checkpoint_manager().close()

    # 落盘
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else REPORTS_DIR / f"report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "dataset_version": version,
        "category": args.category,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": report.model_dump(exclude={"failures"}),
        "failures": [f.model_dump() for f in report.failures],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 评测报告 =====")
    print(f"任务完成率: {report.task_completion_rate:.1%} ({report.passed}/{report.total})")
    print(f"工具调用准确率: {report.tool_accuracy:.1%}")
    print(f"合规通过率: {report.compliance_pass_rate:.1%}")
    print(f"平均耗时: {report.avg_duration_s}s")
    for cat, stat in report.by_category.items():
        print(f"  {cat}: {stat['rate']:.1%} ({int(stat['passed'])}/{int(stat['total'])})")
    print(f"报告已写入: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
