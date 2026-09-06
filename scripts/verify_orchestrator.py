"""T081 验收：LLM Orchestrator 金样本路由一致率初测（真实 LLM）。

用法：
    uv run python -m scripts.verify_orchestrator            # 全部 24 金样本
    uv run python -m scripts.verify_orchestrator --limit 6  # 冒烟子集

流程：临时文件库（保单+案件种子）→ create_default_case_graph（真实 LLM 路由 +
调度 skill）→ 逐案驱动 → 观测最终路由与金额 → 与 cases.json expected 对比。

一致口径：
- expected.route=auto      ⇔ status=auto_issued（且核定金额与期望一致）
- expected.route=human     ⇔ interrupt 挂起（kind=review/escape）
- expected.route=supplement ⇔ interrupt 挂起（kind=supplement）

报告：evals/reports/t081_orchestrator_routing.json；一致率 <0.9 退出码 1（终验线
0.95 在 T089 随全量评测执行）。
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

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.core.config import settings
from services.db.models import Base, Case, ClaimRecord, Policy

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "mock"
REPORT_PATH = ROOT / "evals" / "reports" / "t081_orchestrator_routing.json"


async def _setup(db_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)  # T110：公开 seam（原私有直赋漏网点）

    policies = json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))
    claim_records = json.loads(
        (DATA_DIR / "claim_records.json").read_text(encoding="utf-8")
    )
    async with factory() as s:
        for p in policies:
            s.add(
                Policy(
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
                )
            )
        for cr in claim_records:
            s.add(
                ClaimRecord(
                    claim_no=cr["claim_no"],
                    policy_no=cr["policy_no"],
                    status=cr["status"],
                    applied_amount=Decimal(cr["applied_amount"]),
                    approved_amount=Decimal(cr["approved_amount"]),
                    submitted_at=dt.datetime.fromisoformat(cr["submitted_at"]),
                    updated_at=dt.datetime.fromisoformat(cr["submitted_at"]),
                )
            )
        cases = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
        for c in cases:
            s.add(
                Case(
                    id=c["case_id"],
                    user_id=c["user_id"],
                    policy_no=c["policy_no"],
                    status="received",
                    claimed_amount=Decimal(c["claimed_amount"]),
                    incident_date=dt.date.fromisoformat(c["incident_date"]),
                    incident_description=c["incident_description"],
                    materials=c.get("materials") or [],
                )
            )
        await s.commit()


async def _main(limit: int | None) -> int:
    cases = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    if limit:
        cases = cases[:limit]

    with tempfile.TemporaryDirectory() as tmp:
        await _setup(Path(tmp) / "verify.db")

        from nodes.orchestrator import make_llm_router
        from services.case_store import DbCaseRecorder
        from workflows.case_graph import (
            build_case_graph,
            db_fraud_lookup,
            db_policy_lookup,
        )

        graph = build_case_graph(
            recorder=DbCaseRecorder(),
            policy_lookup=db_policy_lookup,
            fraud_lookup=db_fraud_lookup,
            checkpointer=InMemorySaver(),
            orchestrator_router=make_llm_router(),
        )
        results: list[dict] = []
        for case in cases:
            expected = case["expected"]
            case_id = case["case_id"]
            body = {k: v for k, v in case.items() if k != "expected"}
            body["policy_id"] = body.pop("policy_no")
            config = {"configurable": {"thread_id": case_id}, "recursion_limit": 60}
            try:
                result = await graph.ainvoke(body, config)
                state = graph.get_state(config).values
                if "__interrupt__" in result:
                    kind = result["__interrupt__"][0].value.get("kind")
                    observed = "supplement" if kind == "supplement" else "human"
                else:
                    observed = "auto"
                approved = result.get("approved_amount")
                matched = observed == expected["route"]
                amount_ok = (
                    expected["approved_amount"] is None
                    or observed != "auto"
                    or Decimal(str(approved)) == Decimal(expected["approved_amount"])
                )
                results.append(
                    {
                        "case_id": case_id,
                        "category": expected["category"],
                        "expected_route": expected["route"],
                        "observed_route": observed,
                        "matched": matched,
                        "amount_ok": amount_ok,
                        "approved_amount": str(approved) if approved is not None else None,
                        "routing_calls": state.get("routing_calls"),
                        "error": None,
                    }
                )
                print(f"[{case_id}] expected={expected['route']} observed={observed} "
                      f"matched={matched} amount_ok={amount_ok}")
            # 单案失败不中断评测
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "case_id": case_id,
                        "category": expected["category"],
                        "expected_route": expected["route"],
                        "observed_route": None,
                        "matched": False,
                        "amount_ok": False,
                        "approved_amount": None,
                        "routing_calls": None,
                        "error": str(exc)[:300],
                    }
                )
                print(f"[{case_id}] ERROR {str(exc)[:120]}")
        await session_module.dispose_engine()

    total = len(results)
    matched = sum(1 for r in results if r["matched"] and r["amount_ok"])
    consistency = round(matched / total, 4) if total else 0.0
    passed = consistency >= 0.9
    report = {
        "task": "T081 LLM Orchestrator 金样本路由一致率初测",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "dataset": "data/mock/cases.json",
        "threshold": 0.9,
        "summary": {
            "total": total,
            "matched": matched,
            "consistency": consistency,
            "passed": passed,
        },
        "results": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n路由一致率：{matched}/{total} = {consistency:.1%}（门禁 0.9，"
          f"报告 → {REPORT_PATH.name}）")
    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Orchestrator 路由一致率初测")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个金样本")
    args = parser.parse_args()

    if not settings.llm_api_key:
        print("LLM_API_KEY 未配置，无法执行真实 LLM 路由一致率评测")
        sys.exit(1)
    sys.exit(asyncio.run(_main(args.limit)))


if __name__ == "__main__":
    main()
