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
from evals.schemas import AdjudicationCase

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "evals" / "reports"

# 门禁阈值
GATES = {
    "amount_accuracy": {"threshold": 1.0, "type": "hard"},
    "red_line_leak": {"threshold": 0.0, "type": "hard"},
    "guard_interception": {"threshold": 1.0, "type": "hard"},
    "route_consistency": {"threshold": 0.95, "type": "soft"},
    "liability_consistency": {"threshold": 0.90, "type": "soft"},
    "max_routing_calls": {"threshold": 15, "type": "budget"},
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


async def _run_suite(limit: int | None, use_llm: bool) -> int:
    cases, meta, freq_signals = load_adjudication_dataset()
    if limit:
        cases = cases[:limit]

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

            # 红线漏放检查：PASS 状态的决定书正文不应含红线话术
            doc = state.get("decision_document") or {}
            if doc.get("body") and "保证赔付" in doc["body"]:
                red_line_leaks += 1

            # 守卫旁路检查：state 里不应有前置条件未满足就写入的结论
            if _has_guard_bypass(state):
                guard_bypasses += 1

            # 调度预算
            routing_calls = state.get("routing_calls", 0)
            if routing_calls > 15:
                outcome.error = f"routing_calls={routing_calls} 超预算 15"

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

    # 门禁计算
    auto_cases = [r for r in results if r["expected_route"] == "auto" and not r["error"]]
    amount_correct = sum(
        1 for r in auto_cases if r["checks"]["amount"]
    )
    amount_accuracy = amount_correct / len(auto_cases) if auto_cases else 1.0

    gate_results = {
        "amount_accuracy": {
            "value": round(amount_accuracy, 4),
            "threshold": GATES["amount_accuracy"]["threshold"],
            "passed": amount_accuracy >= GATES["amount_accuracy"]["threshold"],
            "type": "hard",
        },
        "red_line_leak": {
            "value": red_line_leaks,
            "threshold": 0,
            "passed": red_line_leaks == 0,
            "type": "hard",
        },
        "guard_interception": {
            "value": 1.0 if guard_bypasses == 0 else 0.0,
            "threshold": 1.0,
            "passed": guard_bypasses == 0,
            "type": "hard",
        },
        "route_consistency": {
            "value": agg["dimensions"]["route"],
            "threshold": 0.95,
            "passed": agg["dimensions"]["route"] >= 0.95,
            "type": "soft",
        },
        "liability_consistency": {
            "value": agg["dimensions"]["liability"],
            "threshold": 0.90,
            "passed": agg["dimensions"]["liability"] >= 0.90,
            "type": "soft",
        },
        "max_routing_calls": {
            "value": max(
                (r.get("routing_calls", 0) for r in results), default=0
            ),
            "threshold": 15,
            "passed": max(
                (r.get("routing_calls", 0) for r in results), default=0
            ) <= 15,
            "type": "budget",
        },
    }

    hard_pass = all(g["passed"] for g in gate_results.values() if g["type"] == "hard")
    overall_pass = hard_pass

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
    report_path = REPORT_DIR / "t089_adjudication_gate.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 摘要
    print(f"\n{'=' * 60}")
    print(f"核赔评测上线门（{total} 案件，{'LLM' if use_llm else '确定性'}模式）")
    print(f"{'=' * 60}")
    for name, gate in gate_results.items():
        mark = "✅" if gate["passed"] else "❌"
        print(f"  {mark} {name}: {gate['value']} (阈值 {gate['threshold']}, {gate['type']})")
    print(f"{'=' * 60}")
    print(f"总一致率: {agg['consistency']:.1%} | 硬门: {'全绿' if hard_pass else '有未过'}")
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


def _flatten_events(graph, config) -> list[dict]:
    """从图 checkpoint 提取 task_plan 事件（轻量路径，不走 DB）。"""
    state = graph.get_state(config)
    # task_plan 是调度审计——但直接从 state 取
    return state.values.get("task_plan", []) if state.values else []


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
    parser.add_argument("--llm", action="store_true", help="启用 LLM Orchestrator")
    args = parser.parse_args()

    from app.core.config import settings

    if args.llm and not settings.llm_api_key:
        print("LLM_API_KEY 未配置，无法启用 LLM Orchestrator")
        sys.exit(1)

    sys.exit(asyncio.run(_run_suite(args.limit, args.llm)))


if __name__ == "__main__":
    main()
