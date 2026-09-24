"""交付队列消费半区（T103/T153 自 case_jobs 拆出）：CAS 认领 + 执行 + 派发器 + 常驻循环。

- claim_next：单语句 CAS 认领（SQLite/PG 通吃，无 SKIP LOCKED 依赖——D044 单实例契约）
- execute_job：执行到终态（succeeded / queued 重试 / dead + 审计），观测随执行体
- requeue_orphans：启动期回收 running 孤儿（单实例契约）
- 派发器 seam：InlineDispatcher（测试同步档）/ BackgroundDispatcher（生产默认）
- JobLoop：常驻单消费者循环（lifespan 持有）

RESUME 的版本门卫在 services/case_resume_guard.py（T141）。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select, update

from app.core.config import settings
from app.core.logging import get_logger
from services.case_jobs import JobAction
from services.case_resume_guard import resume_invocation
from services.case_store import CaseRecorder
from services.db.models import CaseJob
from services.db.session import get_session_factory
from services.observability import metrics
from services.observability.token_tracker import track_case
from services.observability.tracing import traced_span

log = get_logger(__name__)

# 重试耗尽死信的审计事件 kind（坐席/运维可见的失败凭证）
JOB_FAILED_EVENT = "job_failed"


async def claim_next() -> CaseJob | None:
    """认领下一个到期任务（单语句 CAS：SELECT 到期最早 → 条件 UPDATE 抢占）。

    rowcount=0 即被抢走（或状态已变），返回 None 由调用方重试/空转。
    SQLite 与 PG 通吃（无 SKIP LOCKED 依赖，D044 单实例契约下排他性等价）。
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(CaseJob.id)
                .where(CaseJob.status == "queued", CaseJob.run_after <= dt.datetime.now())
                .order_by(CaseJob.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        result = await session.execute(
            update(CaseJob)
            .where(CaseJob.id == row, CaseJob.status == "queued")
            .values(
                status="running",
                attempt=CaseJob.attempt + 1,
                started_at=dt.datetime.now(),
            )
        )
        await session.commit()
        if result.rowcount == 0:
            return None  # 抢输（并发认领窗口）
        return await session.get(CaseJob, row)


async def execute_job(job: CaseJob, *, graph: Any, recorder: CaseRecorder) -> None:
    """执行一个已认领任务至终态（succeeded / queued 重试 / dead）。

    行级状态更新走独立会话（与图内 recorder 会话互不干扰）；
    观测（CASE_DURATION/CASE_TOKENS）随执行体——路由不再感知。
    """
    started = time.monotonic()
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
            )
        )
        await session.commit()


async def requeue_orphans() -> int:
    """启动期回收：崩溃遗留的 running 孤儿全部回 queued（单实例契约）。

    返回回收行数。执行中途被杀的任务由重跑幂等承载（checkpoint 从上个
    superstep 续跑；重放 superstep 的审计重复为已知噪声，D044 接受）。
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            update(CaseJob)
            .where(CaseJob.status == "running")
            .values(status="queued", run_after=dt.datetime.now())
        )
        await session.commit()
        if result.rowcount:
            log.info("case_jobs_orphans_requeued", count=result.rowcount)
        return result.rowcount or 0


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
    """常驻单消费者循环（background 档，lifespan 持有）。

    start()：先回收孤儿再拉起循环任务；stop()：停止认领 → 排水等待在飞任务
    （超时放弃，行留 running 由下次启动回收）。tick() 独立暴露供确定性测试。
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

    async def start(self) -> None:
        await requeue_orphans()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="case-job-loop")
        log.info("case_job_loop_started", poll_interval_s=self._poll)

    async def tick(self) -> CaseJob | None:
        """处理至多一个到期任务（返回 None=队空）。"""
        job = await claim_next()
        if job is None:
            return None
        await execute_job(job, graph=self._graph, recorder=self._recorder)
        return job

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await self.tick()
            except Exception as exc:  # noqa: BLE001 —— 循环永不因单任务逻辑退出
                log.warning("case_job_loop_tick_error", error=str(exc)[:200])
                job = None
            if job is None:
                await asyncio.sleep(self._poll)

    async def stop(self, timeout_s: float | None = None) -> None:
        """停止认领并排水。超时放弃在飞任务（行留 running，下次启动回收）。"""
        self._stopping = True
        if self._task is None:
            return
        timeout = timeout_s or settings.case_jobs_drain_timeout_s
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            log.warning("case_job_loop_drain_timeout", timeout_s=timeout)
        log.info("case_job_loop_stopped")
