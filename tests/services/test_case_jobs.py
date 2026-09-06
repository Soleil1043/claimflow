"""案件交付队列（T103，D044 混合方案）测试：outbox 凭证 / CAS 认领 / 退避重试 / 死信审计 / 启动回收。

写于实现之前（TDD 红→绿）。这批测试保护的正是"案件不丢/不重跑"的交付语义——
enqueue 不 commit（outbox）、活跃唯一（防双跑）、interrupt=成功终态、
重试耗尽死信 + 审计、崩溃孤儿启动回收。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from services.case_jobs import (
    CaseJobConflictError,
    InlineDispatcher,
    JobAction,
    claim_next,
    enqueue_case_job,
    execute_job,
    job_envelope,
    requeue_orphans,
)
from services.case_service import new_case
from services.db.models import Base, Case, CaseJob
from services.db.session import dispose_engine
from services.observability.metrics import registry as metrics_registry

# ===== 夹具：文件 SQLite + 假图 + 假审计 =====


class FakeGraph:
    """脚本化核赔图：记录调用，按脚本返回/抛错。"""

    def __init__(self, *, result: dict | None = None, error: Exception | None = None):
        self.calls: list[tuple[Any, dict]] = []
        self._result = result if result is not None else {"final_decision": "approved"}
        self._error = error

    async def ainvoke(self, invocation: Any, config: dict | None = None) -> dict:
        self.calls.append((invocation, config or {}))
        if self._error is not None:
            raise self._error
        return self._result


class FakeRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict | None]] = []

    async def event(self, case_id, kind, stage=None, payload=None) -> None:
        self.events.append((case_id, kind, payload))

    async def update_case(self, case_id, **kwargs) -> None:
        pass

    async def save_decision(self, case_id, **kwargs) -> None:
        pass


@pytest.fixture()
async def jobs_db(tmp_path: Path, monkeypatch):
    """文件 SQLite（真实 CaseJob 表）；假图/假审计由各用例注入。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'jobs_test.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module.swap_engine(engine, factory)
    yield factory
    await dispose_engine()  # T110：复位全局（dispose+置 None）


async def _make_case(factory, case_id: str = "CASE-J-0001") -> Case:
    case = new_case(
        case_id=case_id,
        user_id="u-jobs",
        policy_no="POL-2025-0001",
        claimed_amount=Decimal("1000.00"),
        incident_date=dt.date(2026, 8, 1),
        incident_description="队列测试案件",
        materials=[],
    )
    async with factory() as s:
        s.add(case)
        await s.commit()
    return case


async def _job(factory, job_id: int) -> CaseJob:
    async with factory() as s:
        return await s.get(CaseJob, job_id)


def _interrupt_result() -> dict:
    return {
        "__interrupt__": [
            SimpleNamespace(value={"kind": "supplement", "reason": "缺费用清单", "missing": ["费用清单"]})
        ]
    }


# ===== outbox 凭证 =====


async def test_enqueue_is_outbox_no_commit(jobs_db) -> None:
    """enqueue 只插行不提交：rollback → 无凭证（commit 权归调用方）。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        job = await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={"case_id": case.id})
        assert job.id is not None, "flush 应分配主键"
        await s.rollback()
    async with jobs_db() as s:
        assert (await s.execute(select(CaseJob))).scalars().all() == []


async def test_active_job_conflict(jobs_db) -> None:
    """同案件第二个活跃任务 → flush 即撞 partial unique → CaseJobConflictError。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={})
        with pytest.raises(CaseJobConflictError):
            await enqueue_case_job(s, case_id=case.id, action=JobAction.RESUME, payload={})


async def test_new_job_allowed_after_terminal(jobs_db) -> None:
    """终态后活跃约束解除：可再入队（补件恢复即此形态）。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        job = await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={})
        await s.commit()
        job_id = job.id
    async with jobs_db() as s:
        await s.execute(
            update(CaseJob).where(CaseJob.id == job_id).values(status="succeeded", outcome="completed")
        )
        await s.commit()
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RESUME, payload={"kind": "supplement"})
        await s.commit()  # 不抛即通过
    assert True


# ===== 执行语义 =====


async def test_execute_run_completes(jobs_db) -> None:
    """run 任务正常完成 → succeeded/completed；载荷原样进图；thread_id 正确；CASE_DURATION 计时。"""

    case = await _make_case(jobs_db)
    payload = {"case_id": case.id, "claimed_amount": "1000.00"}
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload=payload)
        await s.commit()
    job = await claim_next()
    assert job is not None and job.status == "running" and job.attempt == 1

    before = metrics_registry.get_sample_value("claimflow_case_duration_seconds_count") or 0.0
    graph = FakeGraph()
    await execute_job(job, graph=graph, recorder=FakeRecorder())

    row = await _job(jobs_db, job.id)
    assert row.status == "succeeded" and row.outcome == "completed"
    assert row.interrupt_payload is None
    invocation, config = graph.calls[0]
    assert invocation == payload  # JSON 列往返产生新 dict，按值比较
    assert config["configurable"]["thread_id"] == case.id
    after = metrics_registry.get_sample_value("claimflow_case_duration_seconds_count") or 0.0
    assert after >= before + 1


async def test_execute_interrupt_is_success(jobs_db) -> None:
    """图 interrupt 挂起 = 任务成功终态（outcome=interrupted + 回执快照），不是错误。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={"case_id": case.id})
        await s.commit()
    job = await claim_next()
    await execute_job(job, graph=FakeGraph(result=_interrupt_result()), recorder=FakeRecorder())

    row = await _job(jobs_db, job.id)
    assert row.status == "succeeded"
    assert row.outcome == "interrupted"
    assert row.interrupt_payload["kind"] == "supplement"
    assert row.interrupt_payload["missing"] == ["费用清单"]


async def test_execute_resume_wraps_command(jobs_db) -> None:
    """resume 任务 → 图收到 Command(resume=payload)，不是裸 dict。"""
    case = await _make_case(jobs_db)
    resolution = {"kind": "review", "action": "confirm", "resolved_by": "agent-01"}
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RESUME, payload=resolution)
        await s.commit()
    job = await claim_next()
    graph = FakeGraph()
    await execute_job(job, graph=graph, recorder=FakeRecorder())
    invocation, _ = graph.calls[0]
    assert isinstance(invocation, Command)
    assert invocation.resume == resolution


async def test_execute_error_retries_with_backoff(jobs_db) -> None:
    """执行异常且 attempt < max → 回 queued + run_after 未来 + last_error；退避过后重试成功。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={})
        await s.commit()
    job = await claim_next()
    await execute_job(job, graph=FakeGraph(error=RuntimeError("LLM 抖动")), recorder=FakeRecorder())

    row = await _job(jobs_db, job.id)
    assert row.status == "queued"
    assert row.run_after is not None and row.run_after > dt.datetime.now()
    assert "LLM 抖动" in (row.last_error or "")

    # 退避到期（手动拨近）→ 再认领重试成功
    async with jobs_db() as s:
        await s.execute(
            update(CaseJob).where(CaseJob.id == row.id).values(run_after=dt.datetime.now())
        )
        await s.commit()
    job2 = await claim_next()
    assert job2 is not None and job2.attempt == 2
    await execute_job(job2, graph=FakeGraph(), recorder=FakeRecorder())
    assert (await _job(jobs_db, row.id)).status == "succeeded"


async def test_execute_dead_after_exhaustion_audits(jobs_db) -> None:
    """重试耗尽 → dead + job_failed 审计事件（失败对坐席/运维可见）。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={}, max_attempts=1)
        await s.commit()
    job = await claim_next()
    recorder = FakeRecorder()
    await execute_job(job, graph=FakeGraph(error=RuntimeError("彻底失败")), recorder=recorder)

    row = await _job(jobs_db, job.id)
    assert row.status == "dead" and row.outcome is None
    assert "彻底失败" in (row.last_error or "")
    assert recorder.events and recorder.events[0][1] == "job_failed"


# ===== 认领与恢复 =====


async def test_claim_due_order_and_skip_future(jobs_db) -> None:
    """只认领到期的最早任务；未到期/空队 → None。"""
    case_a = await _make_case(jobs_db, "CASE-J-0001")
    case_b = await _make_case(jobs_db, "CASE-J-0002")
    async with jobs_db() as s:
        await enqueue_case_job(s, case_id=case_a.id, action=JobAction.RUN, payload={})
        await s.commit()
    async with jobs_db() as s:
        enqueue_case_job(
            s, case_id=case_b.id, action=JobAction.RUN, payload={},
        )
        await s.flush()
        # b 推到未来
        await s.execute(
            update(CaseJob)
            .where(CaseJob.case_id == case_b.id)
            .values(run_after=dt.datetime.now() + dt.timedelta(hours=1))
        )
        await s.commit()

    first = await claim_next()
    assert first is not None and first.case_id == case_a.id
    assert await claim_next() is None  # b 未到期，队空


async def test_requeue_orphans_startup_recovery(jobs_db) -> None:
    """启动回收：崩溃遗留的 running 行 → 全部回 queued（单实例契约）。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        job = await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={})
        await s.commit()
        job_id = job.id
    async with jobs_db() as s:
        await s.execute(update(CaseJob).where(CaseJob.id == job_id).values(status="running"))
        await s.commit()

    assert await requeue_orphans() == 1
    assert (await _job(jobs_db, job_id)).status == "queued"


# ===== 派发器与信封 =====


async def test_inline_dispatcher_full_flow(jobs_db) -> None:
    """Inline 派发器（测试/演示档）：dispatch 即执行到终态；缺行响亮失败。"""
    case = await _make_case(jobs_db)
    async with jobs_db() as s:
        job = await enqueue_case_job(s, case_id=case.id, action=JobAction.RUN, payload={"case_id": case.id})
        await s.commit()
        job_id = job.id

    dispatcher = InlineDispatcher(FakeGraph(), FakeRecorder())
    await dispatcher.dispatch(job_id)
    assert (await _job(jobs_db, job_id)).status == "succeeded"

    with pytest.raises(RuntimeError, match="任务行.*不存在"):
        await dispatcher.dispatch(999999)


def test_job_envelope_fields() -> None:
    """job 信封（API 响应投影）字段齐备。"""

    class _Row(SimpleNamespace):
        pass

    env = job_envelope(
        _Row(id=7, action="run", status="succeeded", outcome="completed",
             attempt=1, max_attempts=3, last_error=None)
    )
    assert env["job_id"] == 7
    assert env["action"] == "run"
    assert env["status"] == "succeeded"
    assert env["outcome"] == "completed"
    assert env["attempt"] == 1 and env["max_attempts"] == 3
    assert env["error"] is None


# ===== background 回路（常驻循环：入队 → 认领 → 终态 → 排水） =====


async def test_job_loop_end_to_end_background(jobs_db) -> None:
    """background 档全回路：loop.start（含孤儿回收）→ 入队 → 循环消费到终态 → stop 排水。"""
    import services.case_jobs as cj

    case = await _make_case(jobs_db)
    loop = cj.JobLoop(FakeGraph(), FakeRecorder(), poll_interval_s=0.01)
    await loop.start()

    async with jobs_db() as s:
        job = await enqueue_case_job(
            s, case_id=case.id, action=JobAction.RUN, payload={"case_id": case.id}
        )
        await s.commit()
        job_id = job.id

    for _ in range(200):  # ≤2s 内应到终态
        row = await _job(jobs_db, job_id)
        if row.status == "succeeded":
            break
        await asyncio.sleep(0.01)
    assert (await _job(jobs_db, job_id)).status == "succeeded"

    await loop.stop(timeout_s=2)


# ===== deliver_case_job 收口（T108，D049） =====


async def test_deliver_conflict_returns_none_and_session_usable(jobs_db) -> None:
    """冲突返回 None 且**会话复位可用**——毒化规避是收口的核心价值。"""
    from services.case_jobs import deliver_case_job

    case = await _make_case(jobs_db)
    # 第一单用 no-op 派发（任务留 queued 活跃态）制造真冲突窗口
    from services.case_jobs import BackgroundDispatcher

    async with jobs_db() as s:
        ok = await deliver_case_job(
            s, case_id=case.id, action=JobAction.RUN, payload={"n": 1},
            dispatcher=BackgroundDispatcher(),
        )
        assert ok is not None and ok.status == "queued"

        # 同案件第二个任务：活跃唯一冲突 → None；随后同一会话仍能正常插入其他行
        conflict = await deliver_case_job(
            s, case_id=case.id, action=JobAction.RESUME, payload={"k": 1},
            dispatcher=BackgroundDispatcher(),
        )
        assert conflict is None
        from services.db.models import CaseEvent

        s.add(CaseEvent(case_id=case.id, kind="probe", seq=1))
        await s.commit()  # 毒化的会话在此处会炸——能 commit 即复位成功
    assert True


async def test_deliver_normal_path_returns_refreshed_job(jobs_db) -> None:
    """正常路径：返回刷新过的任务行（succeeded），case 行同步重读。"""
    from services.case_jobs import deliver_case_job

    case = await _make_case(jobs_db)
    dispatcher = InlineDispatcher(FakeGraph(), FakeRecorder())
    async with jobs_db() as s:
        job = await deliver_case_job(
            s,
            case_id=case.id,
            action=JobAction.RUN,
            payload={"case_id": case.id},
            dispatcher=dispatcher,
        )
        assert job is not None
        assert job.status == "succeeded" and job.outcome == "completed"
        assert job.case_id == case.id


async def test_deliver_dispatch_kick_failure_still_returns_job(jobs_db, monkeypatch) -> None:
    """派发触发失败（框架级）→ 告警不阻塞，任务行仍在队由循环认领。"""
    from services.case_jobs import deliver_case_job

    case = await _make_case(jobs_db)

    class _BrokenDispatcher:
        async def dispatch(self, job_id: int) -> None:
            raise RuntimeError("dispatcher down")

    async with jobs_db() as s:
        job = await deliver_case_job(
            s,
            case_id=case.id,
            action=JobAction.RUN,
            payload={"case_id": case.id},
            dispatcher=_BrokenDispatcher(),
        )
        assert job is not None
        assert job.status == "queued", "background 档语义：kick 失败不撤销受理"
