"""单 Agent 基线消融运行器（T160，D074）。

消融问题：Orchestrator-Worker 拆分相对"一个 Agent 拿全部工具独立办案"值多少？
同 153 案、同 LLM（deepseek-flash）、同判分（score_case 五维），两侧全 LLM 真跑：

- 多 Agent 侧：evals.adjudication_suite --llm --llm-workers（与生产
  create_default_case_graph 同构；确定性守卫/公式/静态合规门保留——它们是架构的一部分）
- 单 Agent 侧（本模块）：create_agent 单体 + 全 worker 工具集 + SingleAgentOutput
  结构化输出，独立完成调度/责任/理算/叙述

已知口径差异（即消融对象本身，不抹平）：
- 单 Agent 无静态合规门（图结构保证）——决定书叙述用同一 check_text 做后置红线计数
- 单 Agent 无 worker 并行——时长/工具调用次数单侧观测对比
- worker_sequence 对单体 N/A（checks.sequence 记 False，不计入 matched）
- 单 Agent 模型调用上限 16（多 Agent 侧为 6 worker × 9；给单体合理预算，
  避免把"预算配置"混入"架构差异"）

用法：
    uv run python -m evals.single_agent_baseline                 # 全量 153 案
    uv run python -m evals.single_agent_baseline --limit 5       # 冒烟
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from evals.adjudication_harness import p95, setup_eval_db
from evals.adjudication_metrics import (
    AdjudicationOutcome,
    aggregate,
    load_adjudication_dataset,
    score_case,
)
from evals.schemas import SingleAgentOutput
from services.llm.prompts import SINGLE_AGENT_PROMPT
from services.worker_agent import AgentDefinition, _tool_error_message, derive_tool_trace

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT / "evals" / "reports" / "t160_single_agent_baseline.json"

# 单 Agent 工具集：六阶段 worker 的工具并集（按名称从默认工具图解析）
SINGLE_AGENT_TOOLS = [
    "policy_query",  # 保单核验
    "claim_rule_rag",  # 条款 RAG
    "diagnosis_matcher",  # 诊断匹配（医疗线）
    "claim_calculator",  # 金额理算
    "evaluate_fraud_rules",
    "query_blacklist",
    "query_claims_history",
    "risk_scoring",  # 风控四件
    "compliance_rule_check",
    "claim_draft_link",
    "case_status_query",
]

# 单 Agent 模型调用上限（见模块 docstring 口径说明）
_RUN_MODEL_CALL_LIMIT = 16
_RECURSION_LIMIT = 80


def _agent_def() -> AgentDefinition:
    return AgentDefinition(
        name="single_agent_baseline",
        display_name="单 Agent 基线（消融对照）",
        system_prompt=SINGLE_AGENT_PROMPT,
        tool_names=list(SINGLE_AGENT_TOOLS),
        output_schema=SingleAgentOutput,
        description="一个 Agent 独立完成受理/调度/责任/理算/决定书（T160 消融基线）",
    )


def _build_subgraph(agent_def: AgentDefinition) -> Any:
    """装配单 Agent 子图（镜像 worker_agent.get_worker_subgraph，仅调用上限不同）。"""
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware, ToolErrorMiddleware

    from services.llm.client import get_chat_model
    from tools.factory import get_default_tool_map

    return create_agent(
        model=get_chat_model(),
        tools=agent_def.resolve_tool_objects(get_default_tool_map()),
        system_prompt=agent_def.system_prompt,
        response_format=agent_def.output_schema,
        middleware=[
            ToolErrorMiddleware(on_error=_tool_error_message),
            ModelCallLimitMiddleware(run_limit=_RUN_MODEL_CALL_LIMIT, exit_behavior="end"),
        ],
        name=agent_def.name,
    )


def _instruction(case: Any) -> str:
    """案件事实 → 单 Agent 任务指令（与图版案件输入同字段）。"""
    body = {
        "case_id": case.case_id,
        "user_id": case.user_id,
        "policy_no": case.policy_no,
        "declared_case_type": case.declared_case_type,
        "claimed_amount": case.claimed_amount,
        "incident_date": case.incident_date,
        "incident_description": case.incident_description,
        "materials": case.materials,
    }
    return json.dumps(body, ensure_ascii=False)


def _to_outcome(result: dict[str, Any]) -> AdjudicationOutcome:
    """结构化结论 → 判分观测（refer_kind 折叠回 human/supplement）。"""
    if "route" not in result:
        # invoke_worker 结构化失败降级 {"summary": ...}——按执行失败计
        return AdjudicationOutcome(route="", error="结构化输出缺失 route 键（结构化失败降级）")
    route = (
        "auto"
        if result["route"] == "auto"
        else ("supplement" if result.get("refer_kind") == "supplement" else "human")
    )
    return AdjudicationOutcome(
        route=route,
        final_decision=None,
        approved_amount=str(result.get("approved_amount") or "") or None,
        liability_verdict=result.get("liability"),
        case_type=result.get("case_type"),
        worker_sequence=[],  # 单体无派发序列（sequence 维度 N/A）
    )


def _error_row(case: Any, error: str, started: float) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.expected.category,
        "expected_route": case.expected.route,
        "observed_route": None,
        "checks": {
            "route": False,
            "amount": False,
            "liability": False,
            "case_type": True,
            "sequence": False,
        },
        "matched": False,
        "error": error,
        "tool_calls": 0,
        "duration_s": round(time.perf_counter() - started, 2),
        "tokens": 0,
        "llm_calls": 0,
    }


async def _run_baseline(
    limit: int | None,
    *,
    offset: int = 0,
    concurrency: int = 8,
    out_path: Path = DEFAULT_REPORT,
) -> int:
    cases, _meta, freq_signals = load_adjudication_dataset()
    if offset:
        cases = cases[offset:]
    if limit:
        cases = cases[:limit]

    engine = None
    red_line_leaks = 0
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await setup_eval_db(Path(tmp) / "eval.db", freq_signals)

        import services.db.session as session_module
        from services.observability.token_tracker import track_case
        from tools.compliance.rule_check import check_text

        engine = session_module.get_engine()
        agent_def = _agent_def()
        worker_subgraph = _build_subgraph(agent_def)

        async def _run_one(case: Any) -> tuple[dict[str, Any], bool]:
            started = time.perf_counter()
            acc = None
            try:
                with track_case(case.case_id) as acc_ctx:
                    acc = acc_ctx
                    state = await worker_subgraph.ainvoke(
                        {"messages": [HumanMessage(content=_instruction(case))]},
                        config={"recursion_limit": _RECURSION_LIMIT},
                    )
            except Exception as exc:  # noqa: BLE001
                row = _error_row(case, str(exc)[:300], started)
                row["tokens"] = acc.tokens if acc else 0
                row["llm_calls"] = acc.calls if acc else 0
                return row, False

            # structured_response 提取（invoke_worker 同口径）：缺结构化输出 →
            # 降级 {"summary": 原文}，_to_outcome 判为执行失败
            messages = list(state.get("messages") or [])
            structured = state.get("structured_response")
            if structured is not None:
                result = structured.model_dump()
            else:
                ai_messages = [m for m in messages if isinstance(m, AIMessage) and m.content]
                result = {"summary": str(ai_messages[-1].content)[:500] if ai_messages else ""}

            trace = derive_tool_trace(messages, exclude={"SingleAgentOutput"})
            outcome = _to_outcome(result)
            summary = str(result.get("decision_summary") or "")
            row = score_case(case, outcome) | {
                "tool_calls": len(trace),
                "error": outcome.error,
                "duration_s": round(time.perf_counter() - started, 2),
                "tokens": acc.tokens if acc else 0,
                "llm_calls": acc.calls if acc else 0,
            }
            return row, bool(summary and check_text(summary))

        if concurrency <= 1:
            for case in cases:
                row, leak = await _run_one(case)
                rows.append(row)
                red_line_leaks += int(leak)
        else:
            sem = asyncio.Semaphore(concurrency)

            async def _bounded(c: Any) -> tuple[dict[str, Any], bool]:
                async with sem:
                    return await _run_one(c)

            for row, leak in await asyncio.gather(*(_bounded(c) for c in cases)):
                rows.append(row)
                red_line_leaks += int(leak)

    if engine:
        await engine.dispose()

    agg = aggregate(rows)
    total = agg["total"]
    if total == 0:
        print("无案件可评测")
        return 1

    report = {
        "task": "T160 单 Agent 基线（消融对照，D074）",
        "mode": "llm",
        "total_cases": total,
        "matched": agg["matched"],
        "consistency": agg["consistency"],
        "dimensions": agg["dimensions"],
        "red_line_leaks": red_line_leaks,
        "note": "sequence 维度对单体 N/A（checks.sequence=False 不计入 matched）；无守卫/静态合规门（架构差异本身）",
        "cost": {
            "tokens_total": sum(r.get("tokens") or 0 for r in rows),
            "tokens_per_case_avg": round(sum(r.get("tokens") or 0 for r in rows) / total, 1),
            "tool_calls_avg": round(sum(r.get("tool_calls") or 0 for r in rows) / total, 1),
            "llm_calls_avg": round(sum(r.get("llm_calls") or 0 for r in rows) / total, 1),
            "duration_s_avg": round(sum(r.get("duration_s") or 0.0 for r in rows) / total, 2),
            "duration_s_p95": p95([r.get("duration_s") or 0.0 for r in rows]),
        },
        "failures": agg["failures"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"单 Agent 基线（{total} 案，LLM 模式）")
    print(f"{'=' * 60}")
    print(f"  总一致率: {agg['consistency']:.1%} | matched {agg['matched']}/{total}")
    print(
        f"  维度: route={agg['dimensions']['route']:.1%} amount={agg['dimensions']['amount']:.1%} "
        f"liability={agg['dimensions']['liability']:.1%} case_type={agg['dimensions']['case_type']:.1%}"
    )
    print(
        f"  红线漏放(后置检查): {red_line_leaks} | tokens/案 {report['cost']['tokens_per_case_avg']} | "
        f"工具调用/案 {report['cost']['tool_calls_avg']} | 时长/案 {report['cost']['duration_s_avg']}s"
    )
    print(f"报告 → {out_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="单 Agent 基线消融运行器（T160）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个案件")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 个案件")
    parser.add_argument("--concurrency", type=int, default=8, help="案件并发数（默认 8）")
    parser.add_argument("--out", default=str(DEFAULT_REPORT), help="报告输出路径")
    args = parser.parse_args()

    from app.core.config import settings

    if not settings.llm_api_key:
        print("LLM_API_KEY 未配置，单 Agent 基线必须真实 LLM（无确定性形态）")
        sys.exit(1)

    sys.exit(
        asyncio.run(
            _run_baseline(
                args.limit,
                offset=args.offset,
                concurrency=args.concurrency,
                out_path=Path(args.out),
            )
        )
    )


if __name__ == "__main__":
    main()
