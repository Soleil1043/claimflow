"""客服问答金样本门运行器（T146，D066）。

用法（需真实 LLM Key + 已 ingest 的 Qdrant 本地库）：

    uv run python -m evals.support_suite              # 全量 15 案
    uv run python -m evals.support_suite --limit 3    # 冒烟子集
    uv run python -m evals.support_suite --out p.json # 指定报告路径

与核赔门 adjudication_suite 的关键差异（D066）：
- 客服 Agent 是纯 ReAct 对话，无确定性兜底路径 → 本门必须真 Key，不进默认 CI；
- 进程内直调 services/support/agent.py::reply（临时 SQLite，不依赖起服务）；
- 硬门 = 答案门 100%（关键词组 + 禁止词 + 转人工终态）；
  检索命中率为分层观测（语料小，检索分数波动不应误伤答案门）。

报告落 evals/reports/t146_support_qa_gate.json。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.core.config import settings
from evals.support_metrics import (
    aggregate_support,
    load_support_dataset,
    score_support_case,
)

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "evals" / "reports"
# 进度查询已知案件（与数据集 _meta.known_cases 对齐）
SEED_CASES = [
    {"id": "CASE-2026-0001", "status": "received", "case_type": "medical",
     "approved_amount": None, "final_decision": None},
    {"id": "CASE-2026-0002", "status": "auto_issued", "case_type": "medical",
     "approved_amount": "4640.00", "final_decision": "approved"},
]


async def _setup_db(db_path: Path) -> None:
    """临时库 + mock 种子 + 预置已知案件（adjudication_suite 同骨架）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        from services.db.models import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)

    from services.db.models import Case, ClaimRecord, Policy

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
        for c in SEED_CASES:
            s.add(Case(
                id=c["id"],
                user_id=f"eval-{c['id']}",
                policy_no="POL-2025-0001",
                case_type=c["case_type"],
                status=c["status"],
                claimed_amount=Decimal("15800.00"),
                approved_amount=(
                    Decimal(c["approved_amount"]) if c["approved_amount"] else None
                ),
                incident_date=dt.date(2026, 8, 20),
                incident_description="评测预置案件（客服金样本门）",
                final_decision=c["final_decision"],
            ))
        await s.commit()


async def _rag_sources(question: str) -> list[str]:
    """该问题的检索源（与 claim_rule_rag 工具同口径，top-4）。"""
    from services.rag.retriever import search_kb

    try:
        chunks = await search_kb(query=question, top_k=4)
    except Exception:  # noqa: BLE001 检索故障不阻塞答案评测（rag_hit=False 观测）
        return []
    return [str(c.source_file) for c in chunks]


async def _run_suite(limit: int | None, out_path: str | None) -> int:
    if not settings.llm_api_key:
        print("LLM_API_KEY 未配置——客服门必须真实 LLM（客服无确定性路径，D066）")
        return 1

    cases, meta = load_support_dataset()
    if limit:
        cases = cases[:limit]

    # embedder 预热（BGE-M3 冷加载 ~14s，避免首案超时连坐）
    print("预热 embedder（BGE-M3）…")
    from services.rag.embedder import preload_embedding_model

    preload_embedding_model()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await _setup_db(Path(tmp) / "support_eval.db")
        engine = session_module.get_engine()

        from services.support import store
        from services.support.agent import reply, reset_support_agent_cache

        reset_support_agent_cache()  # 干净子图（防测试态缓存）
        results: list[dict[str, Any]] = []
        for case in cases:
            conv = await store.create_conversation()
            started = time.perf_counter()
            rag_sources: list[str] = []
            try:
                rag_sources = (
                    await _rag_sources(case.question)
                    if case.expected_rag_sources else []
                )
                text = await reply(conv.id, case.question)
                final = await store.get_conversation(conv.id)
                status = final.status if final else "unknown"
            except Exception as exc:  # noqa: BLE001
                text, status = "", f"error:{type(exc).__name__}"
            duration = round(time.perf_counter() - started, 2)
            result = score_support_case(case, text, status, rag_sources)
            result["duration_s"] = duration
            results.append(result)
            mark = "✅" if result["passed"] else "❌"
            rag_mark = (
                f" | rag {'✓' if result['rag_hit'] else '✗'}"
                if result["rag_expected"] else ""
            )
            print(
                f"  {mark} {case.case_id} [{case.category}] {duration}s{rag_mark} "
                f"status={status}"
            )

        await engine.dispose()

    agg = aggregate_support(results)
    overall_pass = agg["answer_pass_rate"] == 1.0

    report = {
        "task": "T146 客服问答金样本门",
        "dataset": "support_qa",
        "dataset_meta": {"version": meta.get("version"), "name": meta.get("name")},
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "llm",
        "model": settings.llm_model,
        "total_cases": agg["total"],
        "passed": agg["passed"],
        "answer_pass_rate": agg["answer_pass_rate"],
        "gate": {
            "answer_pass_rate_100pct": {
                "value": agg["answer_pass_rate"],
                "threshold": 1.0,
                "passed": overall_pass,
                "type": "hard",
            }
        },
        "rag_hit_rate_observed": agg["rag_hit_rate"],  # 分层观测，不阻塞（D066）
        "rag_scope": f"{agg['rag_hit']}/{agg['rag_total']}",
        "overall_passed": overall_pass,
        "by_category": agg["by_category"],
        "failures": agg["failures"],
        "results": results,
        "cost": {
            "duration_s_avg": round(
                sum(r["duration_s"] for r in results) / agg["total"], 2
            ) if results else 0,
        },
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = Path(out_path) if out_path else REPORT_DIR / "t146_support_qa_gate.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"客服问答金样本门（{agg['total']} 案，真实 LLM）")
    print(f"{'=' * 60}")
    gate = report["gate"]["answer_pass_rate_100pct"]
    mark = "✅" if gate["passed"] else "❌"
    print(f"  {mark} answer_pass_rate: {gate['value']} (阈值 100%, hard)")
    if agg["rag_hit_rate"] is not None:
        print(f"  ⭕ rag_hit_rate: {agg['rag_hit_rate']}"
              f" ({agg['rag_hit']}/{agg['rag_total']}, 观测)")
    print(f"{'=' * 60}")
    if agg["failures"]:
        print(f"失败案件（{len(agg['failures'])} 条）：")
        for f in agg["failures"]:
            failed = [k for k, v in f["checks"].items() if not v]
            print(
                f"  {f['case_id']} [{f['category']}] failed={failed}"
                f" missed_groups={f['missed_groups']}"
                f" forbidden_hits={f['forbidden_hits']} status={f['final_status']}"
            )
            print(f"    reply_head: {f['reply_head'][:120]}")
    print(f"报告 → {report_path}")
    return 0 if overall_pass else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="客服问答金样本门（T146，需真实 LLM Key）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 案")
    parser.add_argument("--out", default=None, help="报告输出路径（默认 evals/reports/t146_support_qa_gate.json）")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run_suite(args.limit, args.out)))


if __name__ == "__main__":
    main()
