"""评测运行历史（T052，D028）：git_sha + eval_runs 表读写。

fail-open 原则：历史写入/查询失败只 warn，绝不影响评测本身（评测产物是报告，
历史是附属）。读写经全局 session 工厂（dev=SQLite / prod=PostgreSQL）。
"""

from __future__ import annotations

import subprocess
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from services.db.models import EvalRunRecord

log = get_logger(__name__)

_GIT_SHA: str | None = None


def get_git_sha() -> str:
    """当前代码版本（git rev-parse --short HEAD），进程内缓存；git 不可用回退 unknown。"""
    global _GIT_SHA
    if _GIT_SHA is None:
        try:
            _GIT_SHA = (
                subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                ).stdout.strip()
                or "unknown"
            )
        except Exception:  # noqa: BLE001 git 缺失/非仓库环境不阻塞评测
            _GIT_SHA = "unknown"
    return _GIT_SHA


async def save_run(entry: dict[str, Any]) -> None:
    """按 run_id upsert 一条运行历史（键名与 EvalRunRecord 字段一致）。"""
    try:
        from services.db.session import get_session_factory

        run_id = str(entry["run_id"])
        async with get_session_factory()() as session:
            row = await session.scalar(select(EvalRunRecord).where(EvalRunRecord.run_id == run_id))
            if row is None:
                row = EvalRunRecord(run_id=run_id)
                session.add(row)
            for key, value in entry.items():
                if key != "run_id" and hasattr(row, key):
                    setattr(row, key, value)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 fail-open（D028）
        log.warning("eval_history_save_failed", run_id=entry.get("run_id"), error=str(exc)[:200])


async def list_runs(max_rows: int = 50) -> list[EvalRunRecord]:
    """历史运行（新→旧）；查询失败返回空列表（fail-open）。"""
    try:
        from services.db.session import get_session_factory

        async with get_session_factory()() as session:
            rows = (
                (
                    await session.execute(
                        select(EvalRunRecord).order_by(EvalRunRecord.id.desc()).limit(max_rows)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("eval_history_query_failed", error=str(exc)[:200])
        return []


async def get_run(run_id: str) -> EvalRunRecord | None:
    """按 run_id 查历史；缺失/查询失败返回 None。"""
    try:
        from services.db.session import get_session_factory

        async with get_session_factory()() as session:
            return await session.scalar(select(EvalRunRecord).where(EvalRunRecord.run_id == run_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("eval_history_query_failed", run_id=run_id, error=str(exc)[:200])
        return None
