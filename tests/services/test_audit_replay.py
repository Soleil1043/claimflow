"""审计回放断言单测（T181，D086）：五不变量 × 绿/红/边界矩阵 + 取数薄层集成。

矩阵对应 D086 不变量集合——纯函数 check_invariants 零 IO 全矩阵；audit_case
经临时 SQLite 集成（行 → 平铺 dict 映射与批量分组各覆盖一条路径）。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.audit_replay import (
    INV_BUDGET,
    INV_COMPLIANCE,
    INV_DEAD,
    INV_RECEIPT,
    INV_SEQ,
    audit_all,
    audit_case,
    check_invariants,
)
from services.db.models import Base, CaseEvent, CaseJob, DecisionDocument

CID = "CF-2026-00000001"
T0 = dt.datetime(2026, 10, 10, 12, 0, 0)


def _ev(
    seq: int,
    kind: str,
    *,
    stage: str | None = None,
    payload: dict | None = None,
    at: dt.datetime | None = None,
) -> dict:
    return {
        "seq": seq,
        "kind": kind,
        "stage": stage,
        "payload": payload or {},
        "created_at": at or T0 + dt.timedelta(seconds=seq),
    }


def _routing(seq: int, calls: int, mode: str = "llm", **extra: object) -> dict:
    return _ev(
        seq,
        "routing",
        payload={"calls": calls, "mode": mode, "over_budget": False, **extra},
    )


def _compliance(seq: int, verdict: str = "PASS", amount_ok: bool = True) -> dict:
    return _ev(
        seq,
        "stage_result",
        stage="compliance_gate",
        payload={"verdict": verdict, "amount_consistent": amount_ok},
    )


def _auto_issued(seq: int) -> dict:
    return _ev(seq, "status_change", payload={"status": "auto_issued"})


def _job(
    *,
    status: str = "succeeded",
    outcome: str | None = None,
    interrupt_kind: str | None = None,
) -> dict:
    return {
        "id": 1,
        "status": status,
        "outcome": outcome,
        "interrupt_payload": {"kind": interrupt_kind} if interrupt_kind else None,
    }


def _doc(issued_by: str = "auto") -> dict:
    return {"version": 1, "issued_by": issued_by}


def _green_events() -> list[dict]:
    """绿案事件流：路由 1..3（两次 llm + 一次确定性收敛）→ 合规 PASS → 自动签发。"""
    return [
        _routing(1, 1),
        _routing(2, 2),
        _routing(3, 3, mode="deterministic_fallback"),
        _compliance(4),
        _auto_issued(5),
    ]


# ===== INV1 seq 完整性 =====


def test_green_case_passes_all_invariants() -> None:
    violations = check_invariants(CID, _green_events(), [_job()], [_doc()])
    assert violations == []


def test_empty_events_reported() -> None:
    assert check_invariants(CID, [], [], [])[0].inv == INV_SEQ


def test_seq_gap_detected() -> None:
    events = [_routing(1, 1), _routing(3, 2)]  # seq 2 缺失
    assert any(v.inv == INV_SEQ for v in check_invariants(CID, events, [], []))


def test_duplicate_seq_skips_order_dependent_invariants() -> None:
    """seq 重号使事件顺序不可信：只报 seq 违规，合规前置/路由预算不再产噪声。"""
    events = [
        _routing(1, 1),
        _routing(2, 2),
        _routing(2, 7),  # 重号 + calls 跳号 + 无合规前置
        _auto_issued(3),
    ]
    invs = {x.inv for x in check_invariants(CID, events, [], [])}
    assert invs == {INV_SEQ}


# ===== INV2 合规前置 =====


def test_auto_issued_after_modify_verdict_detected() -> None:
    events = [_routing(1, 1), _compliance(2, verdict="MODIFY"), _auto_issued(3)]
    hits = [v for v in check_invariants(CID, events, [], []) if v.inv == INV_COMPLIANCE]
    assert len(hits) == 1 and "MODIFY" in hits[0].detail


def test_auto_issued_with_inconsistent_amount_detected() -> None:
    events = [_compliance(2, amount_ok=False), _auto_issued(3)]
    assert any(
        v.inv == INV_COMPLIANCE for v in check_invariants(CID, events, [], [])
    )


def test_auto_issued_without_any_compliance_detected() -> None:
    events = [_routing(1, 1), _auto_issued(2)]
    assert any(v.inv == INV_COMPLIANCE for v in check_invariants(CID, events, [], []))


def test_compliance_missing_verdict_field_reported() -> None:
    events = [
        _ev(1, "stage_result", stage="compliance_gate", payload={}),
        _auto_issued(2),
    ]
    hits = [v for v in check_invariants(CID, events, [], []) if v.inv == INV_COMPLIANCE]
    assert hits and "verdict" in hits[0].detail


def test_reject_then_pass_then_auto_is_green() -> None:
    """MODIFY/REJECT 后回环重生成、终局 PASS 再签发——合法（断言取最后一次）。"""
    events = [
        _compliance(1, verdict="REJECT"),
        _compliance(2),  # 修订后复审通过
        _auto_issued(3),
    ]
    assert check_invariants(CID, events, [_job()], [_doc()]) == []


# ===== INV3 路由预算 =====


def test_routing_calls_jump_detected() -> None:
    events = [_routing(1, 1), _routing(2, 2), _routing(3, 4)]  # 3 跳 4
    assert any(v.inv == INV_BUDGET for v in check_invariants(CID, events, [], []))


def test_llm_over_budget_detected() -> None:
    events = [_routing(i, i) for i in range(1, 17)]  # 第 16 次 llm 超预算
    hits = [v for v in check_invariants(CID, events, [], []) if v.inv == INV_BUDGET]
    assert any("超预算" in v.detail for v in hits)


def test_over_budget_then_llm_again_detected() -> None:
    events = [
        _routing(1, 1),
        _routing(2, 2, over_budget=True),
        _routing(3, 3),  # 超预算后不得再 llm
    ]
    hits = [v for v in check_invariants(CID, events, [], []) if v.inv == INV_BUDGET]
    assert any("永久降级" in v.detail for v in hits)


def test_over_budget_converge_deterministically_is_green() -> None:
    """超预算事件本身合法——只要其后走确定性收敛（不再 llm）。"""
    events = [
        *(_routing(i, i) for i in range(1, 16)),
        _routing(16, 16, mode="deterministic_fallback", over_budget=True),
    ]
    assert check_invariants(CID, events, [], []) == []


# ===== INV4 回执单源 =====


def test_upload_without_supplement_receipt_detected() -> None:
    events = [*_green_events(), _ev(6, "material_upload")]
    hits = [v for v in check_invariants(CID, events, [], []) if v.inv == INV_RECEIPT]
    assert hits


def test_batch_upload_one_round_one_receipt_is_green() -> None:
    """同轮多文件批量补件（连续 material_upload 事件）只须一笔回执。"""
    job = _job(outcome="interrupted", interrupt_kind="supplement")
    events = [
        *_green_events(),
        _ev(6, "material_upload"),
        _ev(7, "material_upload"),
        _ev(8, "material_upload"),
    ]
    assert check_invariants(CID, events, [job], [_doc()]) == []


def test_two_upload_rounds_one_receipt_detected() -> None:
    """两轮上传（中间夹图事件）只配一笔回执——第二轮无回执。"""
    one = _job(outcome="interrupted", interrupt_kind="supplement")
    events = [
        _ev(6, "material_upload"),
        _routing(7, 1),
        _ev(8, "material_upload"),
    ]
    hits = [
        v for v in check_invariants(CID, events, [one], []) if v.inv == INV_RECEIPT
    ]
    assert any("上传 2 轮但补件回执仅 1 个" in v.detail for v in hits)


def test_agent_decision_without_interrupted_receipt_detected() -> None:
    events = [_routing(1, 1)]
    docs = [_doc(issued_by="agent:alice")]
    hits = [v for v in check_invariants(CID, events, [_job()], docs) if v.inv == INV_RECEIPT]
    assert any("未经挂起的签发" in v.detail for v in hits)


def test_agent_decision_with_review_receipt_is_green() -> None:
    job = _job(outcome="interrupted", interrupt_kind="review")
    docs = [_doc(), _doc(issued_by="agent:alice")]
    assert check_invariants(CID, _green_events(), [job], docs) == []


def test_upload_with_review_kind_receipt_only_detected() -> None:
    """回执存在但 kind=review（非补件）——不抵补件回执计数。"""
    job = _job(outcome="interrupted", interrupt_kind="review")
    events = [_ev(1, "material_upload")]
    hits = [v for v in check_invariants(CID, events, [job], []) if v.inv == INV_RECEIPT]
    assert any("无回执的恢复" in v.detail for v in hits)


# ===== schema_reset 段边界（版本门卫全新重跑） =====


def test_schema_reset_routing_restart_is_green() -> None:
    """版本门卫全新重跑：calls 归零重启合法（段边界），段内仍须合规前置。"""
    events = [
        _routing(1, 1),
        _routing(2, 2),
        _ev(3, "schema_reset", payload={"expected": 2, "found": 1}),
        _routing(4, 1),
        _routing(5, 2),
        _compliance(6),
        _auto_issued(7),
    ]
    assert check_invariants(CID, events, [_job()], [_doc()]) == []


def test_schema_reset_requires_compliance_reproof() -> None:
    """重跑段内未过合规门直接 auto_issued——旧段证明不作数。"""
    events = [
        _compliance(1),
        _ev(2, "schema_reset", payload={"expected": 2, "found": 1}),
        _routing(3, 1),
        _auto_issued(4),
    ]
    assert any(
        v.inv == INV_COMPLIANCE for v in check_invariants(CID, events, [_job()], [])
    )


# ===== INV5 死信一致 =====


def test_job_failed_with_live_latest_job_detected() -> None:
    events = [_routing(1, 1), _ev(2, "job_failed")]
    hits = [v for v in check_invariants(CID, events, [_job(status="queued")], []) if v.inv == INV_DEAD]
    assert hits


def test_job_failed_with_dead_latest_job_is_green() -> None:
    events = [_routing(1, 1), _ev(2, "job_failed")]
    dead = _job(status="dead")
    assert check_invariants(CID, events, [dead], []) == []


# ===== 取数薄层（临时 SQLite 集成） =====


async def _mk_session(tmp_path: Path) -> async_sessionmaker:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'replay_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_audit_case_maps_rows_and_passes(tmp_path: Path) -> None:
    factory = await _mk_session(tmp_path)
    async with factory() as session:
        session.add_all(
            [
                CaseEvent(case_id=CID, kind="routing", stage="orchestrator", seq=1,
                          payload={"calls": 1, "mode": "llm", "over_budget": False}),
                CaseEvent(case_id=CID, kind="stage_result", stage="compliance_gate", seq=2,
                          payload={"verdict": "PASS", "amount_consistent": True}),
                CaseEvent(case_id=CID, kind="status_change", seq=3,
                          payload={"status": "auto_issued"}),
                CaseJob(case_id=CID, action="RUN", payload={}, status="succeeded",
                        attempt=1, max_attempts=3, run_after=dt.datetime(2026, 10, 10)),
                DecisionDocument(case_id=CID, version=1, title="t", body="b",
                                 conclusion="approved", issued_by="auto"),
            ]
        )
        await session.commit()
    async with factory() as session:
        result = await audit_case(session, CID)
    assert result.passed and result.event_count == 3 and result.doc_count == 1


async def test_audit_case_detects_db_row_violation(tmp_path: Path) -> None:
    factory = await _mk_session(tmp_path)
    other = "CF-2026-00000002"
    async with factory() as session:
        session.add_all(
            [
                # 该案：routing calls 跳号（1→3）+ 坐席签发无回执
                CaseEvent(case_id=other, kind="routing", seq=1,
                          payload={"calls": 1, "mode": "llm", "over_budget": False}),
                CaseEvent(case_id=other, kind="routing", seq=2,
                          payload={"calls": 3, "mode": "llm", "over_budget": False}),
                DecisionDocument(case_id=other, version=1, title="t", body="b",
                                 conclusion="partial", issued_by="agent:bob"),
            ]
        )
        await session.commit()
    async with factory() as session:
        result = await audit_case(session, other)
    invs = {v.inv for v in result.violations}
    assert invs == {INV_BUDGET, INV_RECEIPT}


async def test_audit_all_groups_by_case(tmp_path: Path) -> None:
    factory = await _mk_session(tmp_path)
    async with factory() as session:
        for cid in (CID, "CF-2026-00000002"):
            session.add(
                CaseEvent(case_id=cid, kind="routing", seq=1,
                          payload={"calls": 1, "mode": "llm", "over_budget": False})
            )
        await session.commit()
    async with factory() as session:
        batch = await audit_all(session)
    assert len(batch.cases) == 2
    assert batch.failed_count == 0
    assert batch.by_inv() == {
        "seq_integrity": 0,
        "compliance_before_auto": 0,
        "routing_budget": 0,
        "receipt_before_resume": 0,
        "deadletter_consistency": 0,
    }


@pytest.mark.parametrize("inv", [INV_SEQ, INV_COMPLIANCE, INV_BUDGET, INV_RECEIPT, INV_DEAD])
def test_inv_descriptions_cover_all(inv: str) -> None:
    """报告用的描述表与不变量标识一一对应（防拼写漂移）。"""
    from services.audit_replay import INV_DESCRIPTIONS

    assert INV_DESCRIPTIONS[inv]
