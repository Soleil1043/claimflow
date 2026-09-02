"""评测运行器（T027，T033 扩展 --dataset/--variant）。

用法：
    uv run python -m evals.test_suite                        # 全量 200 条（主数据集）
    uv run python -m evals.test_suite --category simple_faq  # 按分类子集
    uv run python -m evals.test_suite --limit 10             # 前 N 条
    uv run python -m evals.test_suite --out my_report.json   # 指定输出路径
    uv run python -m evals.test_suite --dataset graph_assoc --variant hybrid  # T033 对比
    uv run python -m evals.test_suite --dataset graph_assoc --variant pure_rag

--variant（T033 变体开关，控制 GRAPH_RAG_ENABLED）：
    hybrid    混合召回（默认，图谱开启）
    pure_rag  纯 RAG 基线（图谱关闭，行为与 T031 前一致）

流程：构建主图（真实 LLM + Mock 工具）→ 逐条 ainvoke → metrics 判分 → 聚合 → JSON 落盘。
基线报告：evals/reports/baseline.json（T027 验收产出，后续回归对比用）。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.core.logging import configure_logging, get_logger
from evals.metrics import CaseResult, aggregate, result_from_a06, score_case
from evals.schemas import EvalCase, EvalCategory, EvalDataset
from services.eval_history import get_git_sha

log = get_logger(__name__)

# 数据集注册表：主数据集（四分类 200 条）+ 关联类独立数据集（T033）
DATASETS: dict[str, Path] = {
    "main": Path("evals/datasets/eval_dataset.json"),
    "graph_assoc": Path("evals/datasets/eval_graph_assoc.json"),
}
REPORTS_DIR = Path("evals/reports")


def load_cases(dataset: str, category: str | None, limit: int | None) -> tuple[list[EvalCase], str]:
    """加载数据集并按参数过滤。"""
    ds_path = DATASETS[dataset]
    ds = EvalDataset.model_validate_json(ds_path.read_text(encoding="utf-8"))
    cases = ds.cases
    if category:
        cases = [c for c in cases if c.category == category]
    if limit:
        cases = cases[:limit]
    return cases, ds.version


async def build_eval_graph() -> Any:
    """构建评测主图：真实 LLM + dev profile 零容器（test_suite / ab_test 共用）。

    checkpointer 走全局 CheckpointManager（幂等 start，进程内多变体共享——
    调用方须以不同 thread 前缀隔离变体间的会话状态）。
    """
    _isolate_qdrant_storage()
    import tools.claim  # noqa: F401 注册理赔工具
    import tools.compliance  # noqa: F401 注册合规工具
    import tools.medical  # noqa: F401 注册医疗工具
    from services.memory.short_term import get_checkpoint_manager
    from tools.executor import ToolExecutor
    from tools.registry import get_default_registry
    from workflows.main_graph import build_main_graph

    registry = get_default_registry()
    checkpointer = await get_checkpoint_manager().start()
    return build_main_graph(executor=ToolExecutor(registry), checkpointer=checkpointer)


# Qdrant 存储副本目录（模块级引用，进程退出清理用；None=未创建）
_EVAL_QDRANT_COPY: Path | None = None


def _isolate_qdrant_storage() -> None:
    """dev profile 下把 Qdrant local 存储切到进程级副本（评测基础设施，T068 发现）。

    背景：Qdrant local mode 是目录文件锁，同一目录仅允许一个 client 实例。本地常驻的
    API 服务（uvicorn 8000）与评测进程并发访问 ./data/qdrant 时，评测侧拿锁失败——
    rag_node 静默降级导致向量检索 0 命中（answer 靠图谱与 LLM 兜底，报告失真）。
    副本按 pid 命名，评测只读检索不回写，进程退出由 atexit 清理。
    """
    global _EVAL_QDRANT_COPY
    import atexit
    import shutil
    import tempfile

    from app.core.config import settings

    if settings.is_prod:
        return
    src = Path(settings.qdrant_local_path)
    if not src.exists():
        return
    dst = Path(tempfile.gettempdir()) / f"claimflow_eval_qdrant_{os.getpid()}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    settings.qdrant_local_path = str(dst)
    _EVAL_QDRANT_COPY = dst

    def _cleanup() -> None:
        shutil.rmtree(dst, ignore_errors=True)

    atexit.register(_cleanup)
    log.info("eval_qdrant_isolated", src=str(src), dst=str(dst))


async def run_case(graph: Any, case: EvalCase, thread_prefix: str = "eval") -> CaseResult:
    """执行单条用例：每条独立 thread（避免多轮上下文互相干扰）。

    thread_prefix：A/B 同进程多变体时按变体隔离 checkpoint（T040）。
    """
    thread_id = f"{thread_prefix}-{case.id}"
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
                "shared_data": {},
                "compliance_result": None,
                "compliance_rounds": 0,
                "final_answer": "",
                "need_human_intervention": False,
                "intervention_reason": None,
            },
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": 50},
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
    # 工具轨迹（T047）：与 A06 一致，从 messages 派生（按业务工具白名单过滤）；
    # T050：完整轨迹（含 input/output）单独进 a06["tool_trace"] 供轨迹判分
    from agents.runner import derive_tool_trace
    from tools.factory import get_default_tool_map

    if not a06.get("used_tools"):
        business_tools = set(get_default_tool_map())
        business_trace = [
            t for t in derive_tool_trace(a06.get("messages") or []) if t["tool"] in business_tools
        ]
        a06["tool_trace"] = business_trace
        a06["used_tools"] = [t["tool"] for t in business_trace]

    # 实际 Agent 路由（T050，D026）：task_plan 派生（去重保序）——Worker 子图消息
    # 不带 agent 名，messages 派生不可靠
    route: list[str] = []
    for step in a06.get("task_plan") or []:
        agent = str((step or {}).get("agent") or "")
        if agent and agent not in route:
            route.append(agent)
    a06["agent_route"] = route

    cr = result_from_a06(case, a06, time.perf_counter() - started, error)
    # simple_faq 走 rag_node 直检（不经过 claim_rule_rag 工具），轨迹无 tool 记录——
    # 评测口径与 A06 一致：用 shared_data.rag_context 命中补记 used_tools；
    # 轨迹同步补一条隐式 RAG 调用（D026），保证顺序/次数维度对 FAQ 类也可考核
    if not cr.used_tools and isinstance(a06.get("shared_data"), dict):
        rag_ctx = a06["shared_data"].get("rag_context") or {}
        if rag_ctx.get("results"):
            cr.used_tools = ["claim_rule_rag"]
            cr.tool_trace = [{"agent": "rag", "tool": "claim_rule_rag", "input": {}}]
    return score_case(case, cr)


async def main() -> None:
    parser = argparse.ArgumentParser(description="claimflow 评测运行器")
    parser.add_argument("--category", choices=[c.value for c in EvalCategory], default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--out", default=None, help="报告输出路径（默认 evals/reports/<时间戳>.json）"
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASETS.keys()),
        default="main",
        help="数据集名（main=四分类 200 条；graph_assoc=T033 关联类）",
    )
    parser.add_argument(
        "--variant",
        default="baseline",
        help="实验变体（evals/variants.py 注册表：baseline/hybrid/pure_rag/deepseek-v4-pro…）",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="启用 LLM-as-judge 二层判分（T068：仅 must_include 为空用例，独立口径不并入 passed）",
    )
    args = parser.parse_args()

    configure_logging()

    report: Any = None
    run_error = ""
    try:
        report = await _run_suite(args)
    except Exception as exc:  # noqa: BLE001 评测进程级失败也要留痕（T052）
        run_error = str(exc)[:200]
        log.error("eval_run_failed", error=run_error)
        raise
    finally:
        await _save_cli_history(args, report, run_error)


async def _run_suite(args: argparse.Namespace) -> Any:
    """执行一轮评测：变体生效 → 逐例运行 → 聚合落盘，返回聚合报告（失败向上抛）。"""
    cases, version = load_cases(args.dataset, args.category, args.limit)
    print(
        f"加载 {len(cases)} 条用例（dataset={args.dataset} v{version}, "
        f"category={args.category or '全部'}, variant={args.variant}）"
    )

    # T040：变体统一走注册表（模型/参数/prompt/图谱开关；hybrid/pure_rag 语义与 T033 一致）
    from evals.variants import apply_variant

    spec = apply_variant(args.variant)
    print(f"variant={args.variant}（{spec.description}）")
    # T048：嵌入模型预热（线程池加载）——冷加载 10-20s 会引爆工具守卫超时
    import asyncio as _asyncio

    from services.rag.embedder import preload_embedding_model

    await _asyncio.to_thread(preload_embedding_model)

    graph = await build_eval_graph()

    # LLM-judge（T068，--judge 开关）：惰性导入，未启用零开销
    judge_fn = None
    if args.judge:
        from evals.judge import judge_case, needs_judge

        judge_fn = judge_case
        print("judge=on（must_include 为空用例启用 LLM 二层判分，独立口径）")

    results: list[CaseResult] = []
    passed_count = 0
    for i, case in enumerate(cases, 1):
        cr = await run_case(graph, case)
        if judge_fn is not None and cr.answer and needs_judge(case):
            cr.judge = await judge_fn(case, cr.answer)
        results.append(cr)
        passed_count += cr.passed
        mark = "PASS" if cr.passed else "FAIL"
        print(f"[{i:>3}/{len(cases)}] {mark} {case.id} {case.user_input[:30]}")

    report = aggregate(results)
    from services.memory.short_term import get_checkpoint_manager

    await get_checkpoint_manager().close()

    # 落盘
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = (
        Path(args.out)
        if args.out
        else REPORTS_DIR / f"report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    payload = {
        "dataset_version": version,
        "dataset": args.dataset,
        "category": args.category,
        "variant": args.variant,
        "git_sha": get_git_sha(),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": report.model_dump(exclude={"failures"}),
        "failures": [f.model_dump() for f in report.failures],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 评测报告 =====")
    print(f"任务完成率: {report.task_completion_rate:.1%} ({report.passed}/{report.total})")
    print(f"工具调用准确率: {report.tool_accuracy:.1%}")
    print(f"合规通过率: {report.compliance_pass_rate:.1%}")
    if report.intent_scored:
        print(
            f"意图准确率: {report.intent_accuracy:.1%}（{report.intent_scored} 条标注用例）"
        )
    if report.human_scored or report.human_intervened:
        print(
            f"转人工: recall {report.human_recall:.1%}（{report.human_scored} 条期望）/ "
            f"precision {report.human_precision:.1%}（{report.human_intervened} 条实际转）"
        )
    if report.judge_scored:
        print(
            f"LLM-judge(D033 独立口径): 判过率 {report.judge_pass_rate:.1%}"
            f"（{report.judge_scored} 条，未并入完成率）"
        )
    print(f"平均耗时: {report.avg_duration_s}s")
    print(
        f"检索命中: 向量 {report.avg_vector_hits} 条/例, 图谱事实 {report.avg_graph_hits} 条/例, 图谱覆盖 {report.graph_coverage:.1%}"
    )
    for cat, stat in report.by_category.items():
        print(f"  {cat}: {stat['rate']:.1%} ({int(stat['passed'])}/{int(stat['total'])})")
    traj = report.trajectory
    if any(traj.get(k, {}).get("scored") for k in ("order", "route", "forbidden", "args", "limit")):
        print(
            "轨迹质量(D026): "
            f"顺序 {traj['order']['rate']:.1%}({int(traj['order']['scored'])}条) "
            f"| 路由 {traj['route']['rate']:.1%}({int(traj['route']['scored'])}条) "
            f"| 禁调 {traj['forbidden']['rate']:.1%}({int(traj['forbidden']['scored'])}条) "
            f"| 次数 {traj['limit']['rate']:.1%}({int(traj['limit']['scored'])}条) "
            f"| 入参 {traj['args']['rate']:.1%}({int(traj['args']['scored'])}条) "
            f"| 冗余调用均值 {traj['redundancy']['avg_redundant_calls']}"
        )
    print(f"报告已写入: {out_path}")
    return report


async def _save_cli_history(args: argparse.Namespace, report: Any, run_error: str) -> None:
    """CLI 直接运行的自记历史（T052，D028）；UI 托管运行由 EvalRunManager 写（防双写）。

    在 finally 中调用：report 为 None 即进程级失败，也要留痕；历史写入 fail-open。
    """
    if os.environ.get("EVAL_MANAGED_BY") == "api":
        return
    from services.eval_history import save_run

    await save_run(
        {
            "run_id": f"cli-{time.strftime('%Y%m%d_%H%M%S')}-{uuid.uuid4().hex[:6]}",
            "source": "cli",
            "dataset": args.dataset,
            "variant": args.variant,
            "category": args.category,
            "run_limit": args.limit,
            "status": "completed" if report is not None else "failed",
            "git_sha": get_git_sha(),
            "total": report.total if report else 0,
            "passed": report.passed if report else 0,
            "failed": (report.total - report.passed) if report else 0,
            "task_completion_rate": report.task_completion_rate if report else None,
            "tool_accuracy": report.tool_accuracy if report else None,
            "error": run_error or None,
            "summary": report.model_dump(exclude={"failures"}) if report else None,
            "finished_at": dt.datetime.now(),
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
