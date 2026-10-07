"""核赔评测装配器（T153 自 adjudication_suite 拆出）：环境装配 + 观测提取 + 报告装配。

- setup_eval_db：临时 SQLite + mock 种子 + frequency_signals 相对天数换算
- seed_eval_memories：预置申请人记忆（T100 实验口径）
- extract_outcome / error_result / has_guard_bypass：图执行结果 → 判分观测
- case_tokens_total / p95：成本与延迟指标
- emit_report：报告 dict 装配 + 落盘 + 摘要打印（门禁计算本体在 evals/gates.py）

suite（adjudication_suite.py）保留：案件执行循环 + 门禁接线 + CLI。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from evals.adjudication_metrics import AdjudicationOutcome
from schemas.contract import final_decision_from_verdict

ROOT = Path(__file__).resolve().parent.parent


def case_tokens_total() -> int:
    """CASE_TOKENS 计数器当前累计值（全模型标签求和；差分得单案用量）。

    注意读项目自定义 registry（services.observability.metrics.registry），
    不是 prometheus_client 默认 REGISTRY——CASE_TOKENS 注册在自定义 registry 上。
    """
    from services.observability.metrics import registry

    total = 0
    for metric in registry.collect():
        if metric.name == "claimflow_case_tokens":
            total += int(sum(
                sample.value for sample in metric.samples
                if sample.name.endswith("_total")  # 排除 _created 时间戳样本
            ))
    return total


def p95(values: list[float]) -> float:
    """最近秩法 P95（v1 指标口径延续）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(0.95 * len(ordered)))
    return round(ordered[rank - 1], 2)


async def setup_eval_db(db_path: Path, freq_signals: list[dict[str, Any]] | None = None) -> None:
    """建表 + 种子全量 mock 数据（保单/理赔记录/黑名单走文件）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        from services.db.models import Base

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)


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
        # frequency_signals 相对天数换算（_meta 口径，T122 补实现）：信号保单的静态
        # mock 记录替换为"now - days_ago"记录——"近 90 天"计数不随评测执行时间漂移
        # （原实现只灌静态日期，T089 时窗内 2 条、随日历衰减到 1 条，medium 信号
        # 静默失效，E-0086/88 由金额超线碰巧掩盖）
        if freq_signals:
            from sqlalchemy import delete as sa_delete

            now = dt.datetime.now()
            for sig in freq_signals:
                await s.execute(
                    sa_delete(ClaimRecord).where(
                        ClaimRecord.policy_no == sig["policy_no"]
                    )
                )
                for i, days_ago in enumerate(sig["claims_days_ago"]):
                    submitted = now - dt.timedelta(days=days_ago)
                    s.add(ClaimRecord(
                        claim_no=f"EVAL-FREQ-{sig['policy_no']}-{i}",
                        policy_no=sig["policy_no"],
                        status="approved",
                        applied_amount=Decimal("1000.00"),
                        approved_amount=Decimal("800.00"),
                        submitted_at=submitted,
                        updated_at=submitted,
                    ))
        await s.commit()


async def seed_eval_memories(cases: list[Any]) -> None:
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


def extract_outcome(
    graph: Any, config: dict, result: dict, state: dict
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


def error_result(case: Any, error: str) -> dict[str, Any]:
    """执行异常的结果行（各判 false，错误入列）。"""
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


def has_guard_bypass(state: dict) -> bool:
    """守卫旁路检测：decision 存在但必做集不全 → 旁路。"""
    from nodes.guards import stage_done
    from schemas.stages import MUST_COMPLETE

    if not state.get("decision"):
        return False
    return any(not stage_done(state, w) for w in MUST_COMPLETE)


def emit_report(
    *,
    results: list[dict[str, Any]],
    agg: dict[str, Any],
    gate_results: dict[str, Any],
    overall_pass: bool,
    robustness_block: dict[str, Any] | None,
    dataset_name: str,
    use_llm: bool,
    model: str | None,
    report_path: Path,
) -> None:
    """报告 dict 装配 + 落盘 + 摘要打印（T089/T124 口径不变）。"""
    total = agg["total"]
    report = {
        "task": (
            "T124 核赔对抗回归门" if dataset_name == "adversarial"
            else "T159 对抗 hold-out 盲测门" if dataset_name == "adversarial_holdout"
            else "T089 核赔评测上线门"
        ),
        "dataset": dataset_name,
        "robustness": robustness_block,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "llm" if use_llm else "deterministic",
        "model": model,
        "total_cases": total,
        "matched": agg["matched"],
        "consistency": agg["consistency"],
        "gates": gate_results,
        "overall_passed": overall_pass,
        "by_category": agg["by_category"],
        "failures": agg["failures"],
        # 成本量化（T123，证据缺口#1）：tokens/延迟按案分布
        "cost": {
            "tokens_total": sum(r.get("tokens") or 0 for r in results),
            "tokens_per_case_avg": round(
                sum(r.get("tokens") or 0 for r in results) / total, 1
            ),
            "duration_s_avg": round(
                sum(r.get("duration_s") or 0.0 for r in results) / total, 2
            ),
            "duration_s_p95": p95([r.get("duration_s") or 0.0 for r in results]),
        },
    }

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
