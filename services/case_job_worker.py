"""交付队列消费半区（T103/T153 自 case_jobs 拆出）：租约认领 + 执行 + 派发器 + 常驻循环。

多实例契约（T155，D071）：
- claim_next：CAS 认领"queued 到期 ∨ running 租约过期（含 NULL=旧版本孤儿）"
  （SQLite/PG 通吃，无 SKIP LOCKED 依赖——条件 UPDATE 单语句即排他）
- 心跳续租：执行期每 ttl/3 刷新己方租约；活租约行其他实例不可接管
- 崩溃接管：实例死亡后租约自然过期回流通池——不再有启动期全量孤儿回收
  （旧机制会误伤他实例在飞任务，滚动发布场景必现双跑）
- 终态/重试/停止超时统一清租约或释放己方行

RESUME 的版本门卫在 services/case_resume_guard.py（T141）。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket
import time
import uuid
from contextlib import suppress
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, or_, select, update

from app.core.config import settings
from app.core.logging import get_logger
from services.case_store import CaseRecorder
from services.db.models import CaseJob
from services.db.session import get_session_factory
from services.observability import metrics
from services.observability.metrics import set_queue_depth
from services.observability.token_tracker import track_case
from services.observability.tracing import traced_span

log = get_logger(__name__)


class JobAction(StrEnum):
    """交付动作：run 全新核赔（payload=图 input）｜ resume 恢复（payload=resume 载荷）。

    T155 自 case_jobs 迁入消费半区：worker → case_jobs 的模块级反向引用会与
    facade re-export 成环（先 import worker 即爆），依赖改为严格单向
    case_jobs → worker，公开导入路径经 facade 不变。
    """

    RUN = "run"
    RESUME = "resume"


# 重试耗尽死信的审计事件 kind（坐席/运维可见的失败凭证）
JOB_FAILED_EVENT = "job_failed"

# 本实例标识（租约持有者）：主机+PID+随机后缀，日志与 locked_by 列可追溯
INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _claimable(now: dt.datetime) -> Any:
    """可认领谓词：queued 到期，或 running 但租约已过期/为空（崩溃孤儿/旧版本遗留）。"""
    return or_(
        # queued 必须到期（退避）；running 抢占只看租约
        (CaseJob.status == "queued") & (CaseJob.run_after <= now),
        (CaseJob.status == "running")
        & ((CaseJob.lease_expires_at.is_(None)) | (CaseJob.lease_expires_at <= now)),
    )


async def refresh_queue_depth() -> None:
    """查询并刷新队列深度 Gauge（T154 告警信号；查询失败静默保持旧值）。"""
    factory = get_session_factory()
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(CaseJob.status, func.count(CaseJob.id))
                    .where(CaseJob.status.in_(("queued", "running")))
                    .group_by(CaseJob.status)
                )
            ).all()
        by_status = dict(rows)
        set_queue_depth(
            queued=by_status.get("queued", 0),
            running=by_status.get("running", 0),
        )
    except Exception:  # noqa: BLE001 埋点容错：观测查询失败不影响消费循环
        pass


async def claim_next() -> CaseJob | None:
    """认领下一个可接管任务（单语句 CAS：SELECT 最早可认领 → 条件 UPDATE 抢占）。

    UPDATE 的 WHERE 重带可认领谓词（取新 now）——SELECT 与 UPDATE 之间被他实例
    抢先续租/认领时 rowcount=0，返回 None 由调用方重试/空转。
    抢占成功即持有租约（locked_by=INSTANCE_ID + lease 过期时间）。
    """
    factory = get_session_factory()
    async with factory() as session:
        now = dt.datetime.now()
        row = (
            await session.execute(
                select(CaseJob.id)
                .where(_claimable(now))
                .order_by(CaseJob.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        ttl = dt.timedelta(seconds=settings.case_jobs_lease_ttl_s)
        result = await session.execute(
            update(CaseJob)
            .where(CaseJob.id == row, _claimable(dt.datetime.now()))
            .values(
                status="running",
                attempt=CaseJob.attempt + 1,
                started_at=dt.datetime.now(),
                locked_by=INSTANCE_ID,
                lease_expires_at=dt.datetime.now() + ttl,
            )
        )
        await session.commit()
        if result.rowcount == 0:
            return None  # 抢输（并发认领/续租窗口）
        return await session.get(CaseJob, row)


async def _extend_lease(job_id: int) -> None:
    """续租：仅当行仍是己方持有的 running 时延长（被接管后不再续——防僵尸写）。"""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(CaseJob)
            .where(
                CaseJob.id == job_id,
                CaseJob.locked_by == INSTANCE_ID,
                CaseJob.status == "running",
            )
            .values(
                lease_expires_at=dt.datetime.now()
                + dt.timedelta(seconds=settings.case_jobs_lease_ttl_s)
            )
        )
        await session.commit()


async def _heartbeat_loop(job_id: int) -> None:
    """执行期心跳：每 ttl/3 续租一次；单次续租失败只记日志（下轮再试）。"""
    interval = max(settings.case_jobs_lease_ttl_s / 3, 0.05)
    while True:
        await asyncio.sleep(interval)
        try:
            await _extend_lease(job_id)
        except Exception as exc:  # noqa: BLE001 —— 续租失败不中断执行：租约到期前有重试窗口
            log.warning("case_job_lease_extend_failed", job_id=job_id, error=str(exc)[:200])


async def _release_own(job_id: int) -> None:
    """释放己方在飞行（停止排水超时后调用）：行回 queued，立即可被任意实例接管。"""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(CaseJob)
            .where(
                CaseJob.id == job_id,
                CaseJob.locked_by == INSTANCE_ID,
                CaseJob.status == "running",
            )
            .values(
                status="queued",
                locked_by=None,
                lease_expires_at=None,
                run_after=dt.datetime.now(),
            )
        )
        await session.commit()


async def execute_job(job: CaseJob, *, graph: Any, recorder: CaseRecorder) -> None:
    """执行一个已认领任务至终态（succeeded / queued 重试 / dead）。

    行级状态更新走独立会话（与图内 recorder 会话互不干扰）；
    观测（CASE_DURATION/CASE_TOKENS）随执行体——路由不再感知。
    执行期心跳续租并行运行（T155 多实例）：图跑多久租约就续多久。
    """
    started = time.monotonic()
    # 延迟导入：guard 顶部反向引用本模块的 JobAction，模块级互引会成环
    from services.case_resume_guard import resume_invocation

    heartbeat = asyncio.create_task(_heartbeat_loop(job.id))
    try:
        with track_case(job.case_id):
            if job.action == JobAction.RESUME.value:
                invocation: Any = await resume_invocation(job, graph=graph, recorder=recorder)
            else:
                invocation = job.payload
            with traced_span("case.deliver", case_id=job.case_id, action=job.action):
                result = await graph.ainvoke(
                    invocation,
                    config={
                        "configurable": {"thread_id": job.case_id},
                        "recursion_limit": 60,
                    },
                )
        if job.action == JobAction.RUN.value:
            metrics.record_case_duration(time.monotonic() - started)

        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if interrupts:
            value = getattr(interrupts[0], "value", None)
            payload = value if isinstance(value, dict) else {}
            await _finish(
                job.id,
                status="succeeded",
                outcome="interrupted",
                interrupt_payload=payload,
            )
        else:
            await _finish(job.id, status="succeeded", outcome="completed")
    except Exception as exc:  # noqa: BLE001 —— 交付级异常：重试或死信，不向上抛
        if job.attempt >= job.max_attempts:
            await _finish(job.id, status="dead", outcome=None, error=str(exc)[:500])
            try:
                await recorder.event(
                    job.case_id,
                    JOB_FAILED_EVENT,
                    payload={"attempts": job.attempt, "error": str(exc)[:300]},
                )
            except Exception:  # noqa: BLE001 —— 审计失败不掩盖死信状态
                log.warning("job_failed_audit_missed", job_id=job.id)
            log.warning("case_job_dead", job_id=job.id, case_id=job.case_id,
                        attempts=job.attempt, error=str(exc)[:200])
        else:
            delay = settings.case_jobs_backoff_base_s * (2 ** (job.attempt - 1))
            await _retry(job.id, error=str(exc)[:500], delay_s=delay)
            log.warning("case_job_retry_scheduled", job_id=job.id,
                        attempt=job.attempt, delay_s=delay, error=str(exc)[:200])
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def _finish(
    job_id: int,
    *,
    status: str,
    outcome: str | None,
    interrupt_payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(CaseJob)
            .where(CaseJob.id == job_id)
            .values(
                status=status,
                outcome=outcome,
                interrupt_payload=interrupt_payload,
                last_error=error,
                finished_at=dt.datetime.now(),
                locked_by=None,
                lease_expires_at=None,
            )
        )
        await session.commit()


async def _retry(job_id: int, *, error: str, delay_s: float) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            update(CaseJob)
            .where(CaseJob.id == job_id)
            .values(
                status="queued",
                last_error=error,
                run_after=dt.datetime.now() + dt.timedelta(seconds=delay_s),
                locked_by=None,
                lease_expires_at=None,
            )
        )
        await session.commit()


# ===== 派发器（seam：执行时机的两个真 adapter） =====


@runtime_checkable
class CaseJobDispatcher(Protocol):
    """请求路径的交付触发点。"""

    async def dispatch(self, job_id: int) -> None: ...


class InlineDispatcher:
    """同步执行 adapter（测试/兼容档）：dispatch 即执行到终态，响应保留同步语义。"""

    def __init__(self, graph: Any, recorder: CaseRecorder):
        self._graph = graph
        self._recorder = recorder

    async def dispatch(self, job_id: int) -> None:
        factory = get_session_factory()
        async with factory() as session:
            job = await session.get(CaseJob, job_id)
        if job is None:
            msg = f"任务行 {job_id} 不存在（enqueue 未提交？）"
            raise RuntimeError(msg)
        await execute_job(job, graph=self._graph, recorder=self._recorder)


class BackgroundDispatcher:
    """后台循环 adapter（生产默认）：no-op——常驻消费者 ≤ poll_interval 内认领。"""

    async def dispatch(self, job_id: int) -> None:
        return None


def make_case_dispatcher(graph: Any, recorder: CaseRecorder) -> CaseJobDispatcher:
    """按 settings.case_jobs_execution 装配派发器（inline | background）。"""
    if settings.case_jobs_execution == "inline":
        return InlineDispatcher(graph, recorder)
    return BackgroundDispatcher()


class JobLoop:
    """常驻单消费者循环（background 档，lifespan 持有；每实例一个，多实例并存安全）。

    start()：直接拉起循环任务——不做任何全局回收（多实例下他实例可能在飞，
    崩溃遗留由租约到期接管）。stop()：停止认领 → 排水等待在飞任务（超时取消
    并释放己方行回 queued，立即可被其他实例接管）。tick() 独立暴露供确定性测试。
    """

    def __init__(
        self,
        graph: Any,
        recorder: CaseRecorder,
        *,
        poll_interval_s: float | None = None,
    ):
        self._graph = graph
        self._recorder = recorder
        self._poll = poll_interval_s or settings.case_jobs_poll_interval_s
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._current_job_id: int | None = None  # 停止超时释放目标（T155）

    async def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="case-job-loop")
        log.info("case_job_loop_started", poll_interval_s=self._poll, instance=INSTANCE_ID)

    async def tick(self) -> CaseJob | None:
        """处理至多一个到期任务（返回 None=队空）。"""
        job = await claim_next()
        if job is None:
            return None
        self._current_job_id = job.id
        try:
            await execute_job(job, graph=self._graph, recorder=self._recorder)
        finally:
            self._current_job_id = None
        return job

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await refresh_queue_depth()  # T154：积压告警信号每 tick 刷新
                job = await self.tick()
            except Exception as exc:  # noqa: BLE001 —— 循环永不因单任务逻辑退出
                log.warning("case_job_loop_tick_error", error=str(exc)[:200])
                job = None
            if job is None:
                await asyncio.sleep(self._poll)

    async def stop(self, timeout_s: float | None = None) -> None:
        """停止认领并排水。超时取消在飞任务并释放己方行（回 queued 可被接管）。"""
        self._stopping = True
        if self._task is None:
            return
        timeout = timeout_s or settings.case_jobs_drain_timeout_s
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            log.warning("case_job_loop_drain_timeout", timeout_s=timeout)
            if self._current_job_id is not None:
                try:
                    await _release_own(self._current_job_id)
                except Exception as exc:  # noqa: BLE001 —— 释放失败：租约到期自然回流通池
                    log.warning(
                        "case_job_loop_release_failed",
                        job_id=self._current_job_id,
                        error=str(exc)[:200],
                    )
        log.info("case_job_loop_stopped", instance=INSTANCE_ID)
