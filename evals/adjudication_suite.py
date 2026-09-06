"""核赔评测接入与上线门（Phase 8 T089，D037/D039）。

用法：
    uv run python -m evals.adjudication_suite                # 全量 150 案（确定性编排，零 LLM）
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
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.core.config import settings
from evals.adjudication_metrics import (
    AdjudicationOutcome,
    aggregate,
    load_adjudication_dataset,
    score_case,
)
from evals.gates import evaluate_gates, overall_passed
from evals.schemas import AdjudicationCase
from schemas import contract
from schemas.contract import final_decision_from_verdict
from tools.compliance.rule_check import check_text

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "evals" / "reports"

# 门禁阈值
GATES = {
    "amount_accuracy": {"threshold": 1.0, "type": "hard"},
    "red_line_leak": {"threshold": 0.0, "type": "hard"},
    "guard_interception": {"threshold": 1.0, "type": "hard"},
    "route_consistency": {"threshold": 0.95, "type": "soft"},
    "liability_consistency": {"threshold": 0.90, "type": "soft"},
    "max_routing_calls": {"threshold": contract.ROUTING_CALL_BUDGET, "type": "budget"},
}


async def _setup_db(db_path: Path) -> None:
    """建表 + 种子全量 mock 数据（保单/理赔记录/黑名单走文件）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        from services.db.models import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine
    session_module._session_factory = factory

    import datetime as dt

    from services.db.models import ClaimRecord, Policy

    policies = json.loads(
        (ROOT / "data" / "mock" / "policies.json").read_text(encoding="utf-8")
    )
    claim_records = json.loads(
        (ROOT / "data" / "mock" / "claim_records.json").read_text(encoding="utf-8")
    )
    async with factory() as s:
        for p in policies:
            s.add(Policy(
                policy_no=p["policy_no"],
                holder_name=p["holder_name"],
                holder_id_card=p["holder_id_card"],
                product_name=p["product_name"],
                product_type=p["product_type"],
                coverage_amount=Decimal(p["coverage_amount"]),
                deductible=Decimal(p["deductible"]),
                payout_ratio=Decimal(p["payout_ratio"]),
                effective_date=dt.date.fromisoformat(p["effective_date"]),
                expiry_date=dt.date.fromisoformat(p["expiry_date"]),
                status=p["status"],
            ))
        for cr in claim_records:
            submitted = dt.datetime.fromisoformat(cr["submitted_at"])
            s.add(ClaimRecord(
                claim_no=cr["claim_no"],
                policy_no=cr["policy_no"],
                status=cr["status"],
                applied_amount=Decimal(cr["applied_amount"]),
                approved_amount=Decimal(cr["approved_amount"]),
                submitted_at=submitted,
                updated_at=submitted,
            ))
        await s.commit()


async def _seed_memories(cases: list[Any]) -> None:
    """预置申请人记忆（T100 实验口径）：从数据集期望值渲染每用户至多 4 条终态档案。

    分块评测时每进程独立 InMemoryStore——种子让记忆注入在每个分块都满载荷生效。
    """
    from services.memory.case_memory import put_case_memory, render_case_memory

    per_user: dict[str, list[Any]] = {}
    for c in cases:
        seen = per_user.setdefault(c.user_id, [])
        if len(seen) >= 4:
            continue
        seen.append(c)
    for user_id, user_cases in per_user.items():
        for c in user_cases:
            exp = c.expected
            outcome = "auto_issued" if exp.route == "auto" else "referred"
            # 终态判定单源（T107）：与 auto_adjudicate 同一规则；转人工案（缺件/
            # 受理分类）无自动终态——记忆终态即 referred
            decision = (
                "referred"
                if outcome != "auto_issued"
                else final_decision_from_verdict(exp.liability or "covered")
            )
            record, embed_text = render_case_memory(
                case_id=c.case_id,
                user_id=user_id,
                case_type=c.declared_case_type or "medical",
                outcome=outcome,
                final_decision=decision,
                approved_amount=exp.approved_amount,
                reason=exp.note or exp.category,
                incident_date=c.incident_date,
            )
            await put_case_memory(record, embed_text)
    print(f"  已预置申请人记忆：{len(per_user)} 用户 × ≤4 条")


async def _run_suite(
    limit: int | None,
    use_llm: bool,
    *,
    offset: int = 0,
    memory_routing: bool = False,
    seed_memories: bool = False,
    out_path: str | None = None,
) -> int:
    cases, meta, freq_signals = load_adjudication_dataset()
    if offset:
        cases = cases[offset:]
    if limit:
        cases = cases[:limit]

    settings.memory_in_routing = memory_routing
    if seed_memories:
        await _seed_memories(cases)

    engine = None
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await _setup_db(Path(tmp) / "eval.db")
        engine = session_module._engine

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

            try:
                result = await graph.ainvoke(body, config)
                state = graph.get_state(config).values
            except Exception as exc:  # noqa: BLE001
                results.append(_error_result(case, str(exc)[:300]))
                continue

            # 提取观测
            outcome = _extract_outcome(graph, config, result, state)

            # 红线漏放检查（T097）：与运行时合规门同一实现（check_text），不再用弱化子串
            doc = state.get("decision_document") or {}
            if doc.get("body") and check_text(doc["body"]):
                red_line_leaks += 1

            # 守卫旁路检查：state 里不应有前置条件未满足就写入的结论
            if _has_guard_bypass(state):
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
    gate_results = evaluate_gates(
        results, red_line_leaks=red_line_leaks, guard_bypasses=guard_bypasses
    )

    overall_pass = overall_passed(gate_results)

    report = {
        "task": "T089 核赔评测上线门",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "llm" if use_llm else "deterministic",
        "model": settings.llm_model if use_llm else None,
        "total_cases": total,
        "matched": agg["matched"],
        "consistency": agg["consistency"],
        "gates": gate_results,
        "overall_passed": overall_pass,
        "by_category": agg["by_category"],
        "failures": agg["failures"],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = Path(out_path) if out_path else REPORT_DIR / "t089_adjudication_gate.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 摘要
    print(f"\n{'=' * 60}")
    print(f"核赔评测上线门（{total} 案件，{'LLM' if use_llm else '确定性'}模式）")
    print(f"{'=' * 60}")
    for name, gate in gate_results.items():
        mark = "✅" if gate["passed"] else "❌"
        print(f"  {mark} {name}: {gate['value']} (阈值 {gate['threshold']}, {gate['type']})")
    print(f"{'=' * 60}")
    print(f"总一致率: {agg['consistency']:.1%} | 硬门: {'全绿' if overall_pass else '有未过'}")
    print(f"报告 → {report_path}")

    if agg["failures"]:
        print(f"\n失败案件（{len(agg['failures'])} 条）：")
        for f in agg["failures"][:10]:
            failed_dims = [k for k, v in f["checks"].items() if not v]
            print(f"  {f['case_id']} [{f['category']}] dim={failed_dims} "
                  f"expected={f['expected_route']} observed={f['observed_route']}")

    return 0 if overall_pass else 1


def _extract_outcome(
    graph, config: dict, result: dict, state: dict
) -> AdjudicationOutcome:
    """从图执行结果提取观测事实。"""
    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        kind = str(payload.get("kind", "review"))
        route = "supplement" if kind == "supplement" else "human"
        return AdjudicationOutcome(route=route, kind=kind)

    route = "auto"
    # worker 序列从已完成的阶段 channel 推导（STAGE_SPECS 顺序 = 标准管线序）
    from schemas.stages import STAGE_CHANNELS, WORKER_TARGETS

    worker_sequence = [
        str(w) for w in WORKER_TARGETS if state.get(STAGE_CHANNELS[w]) is not None
    ]
    return AdjudicationOutcome(
        route=route,
        final_decision=state.get("final_decision"),
        approved_amount=str(state.get("approved_amount") or ""),
        liability_verdict=str((state.get("liability") or {}).get("verdict") or ""),
        case_type=str(state.get("case_type") or ""),
        worker_sequence=worker_sequence,
    )


def _error_result(case: AdjudicationCase, error: str) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "category": case.expected.category,
        "expected_route": case.expected.route,
        "observed_route": None,
        "checks": {
            "route": False, "amount": False, "liability": False,
            "case_type": True, "sequence": False,
        },
        "matched": False,
        "error": error,
    }


def _has_guard_bypass(state: dict) -> bool:
    """守卫旁路检测：decision 存在但必做集不全 → 旁路。"""
    from nodes.orchestrator import MUST_COMPLETE, stage_done

    if not state.get("decision"):
        return False
    return any(not stage_done(state, w) for w in MUST_COMPLETE)


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
        )
    ))


if __name__ == "__main__":
    main()
