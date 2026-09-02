"""评测运行管理器（T051，D027）：子进程隔离执行 evals.test_suite + 进度解析。

D027：评测会重建主图并 close 全局 checkpointer/嵌入单例，API 进程内执行会污染服务常驻
状态——子进程复用 CLI 全部语义（嵌入预热/工具守卫/报告落盘），本模块只做生命周期管理
（单活跃运行守卫）与 stdout 进度转发。运行注册表存内存，磁盘报告 JSON 即持久历史。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.logging import get_logger
from services.eval_history import get_git_sha, save_run

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 运行器进度行："[  1/3] PASS FAQ-001 阑尾炎手术有等待期吗"
_PROGRESS_RE = re.compile(r"^\[\s*(\d+)/(\d+)\]\s+(PASS|FAIL)\s+(\S+)")

EvalStatus = Literal["running", "completed", "failed"]


class RunAlreadyActiveError(RuntimeError):
    """已有评测在运行（单活跃守卫，API 层映射 409）。"""


@dataclass
class EvalRunParams:
    """一次评测运行的参数（与 CLI --dataset/--category/--limit/--variant 一一对应）。"""

    dataset: str = "main"
    category: str | None = None
    limit: int | None = None
    variant: str = "baseline"


@dataclass
class EvalRun:
    """一次评测运行的状态记录。"""

    run_id: str
    params: EvalRunParams
    status: EvalStatus = "running"
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    finished_at: str | None = None
    current: int = 0
    total: int = 0
    passed: int = 0
    failed: int = 0
    return_code: int | None = None
    git_sha: str = "unknown"
    report_name: str | None = None
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=200))


def _default_command(params: EvalRunParams, run_id: str, reports_dir: Path) -> list[str]:
    """评测命令：与 `uv run python -m evals.test_suite` 等价（报告固定 ui_<run_id>.json）。"""
    argv = [
        sys.executable,
        "-m",
        "evals.test_suite",
        "--dataset",
        params.dataset,
        "--variant",
        params.variant,
        "--out",
        str(reports_dir / f"ui_{run_id}.json"),
    ]
    if params.category:
        argv += ["--category", params.category]
    if params.limit:
        argv += ["--limit", str(params.limit)]
    return argv


class EvalRunManager:
    """评测运行注册表 + 子进程驱动（进程内单活跃运行）。

    command_builder 供测试注入假命令；reports_dir 决定报告落盘/查询目录。
    """

    def __init__(
        self,
        reports_dir: Path = PROJECT_ROOT / "evals" / "reports",
        command_builder: Callable[[EvalRunParams, str, Path], list[str]] = _default_command,
    ) -> None:
        self.reports_dir = reports_dir
        self._command_builder = command_builder
        self._runs: dict[str, EvalRun] = {}
        self._active_id: str | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._proc: asyncio.subprocess.Process | None = None

    def list_runs(self) -> list[EvalRun]:
        """全部运行记录（新→旧）。"""
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def get_run(self, run_id: str) -> EvalRun | None:
        return self._runs.get(run_id)

    async def start_run(self, params: EvalRunParams) -> EvalRun:
        """启动评测子进程；已有活跃运行时抛 RunAlreadyActiveError。"""
        if self._active_id is not None:
            raise RunAlreadyActiveError(f"已有评测在运行：{self._active_id}")

        run_id = f"{time.strftime('%Y%m%d_%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run = EvalRun(run_id=run_id, params=params)
        run.git_sha = get_git_sha()
        self._runs[run_id] = run
        self._active_id = run_id

        argv = self._command_builder(params, run_id, self.reports_dir)
        # 子进程 stdout 固定 utf-8：Windows 管道默认本地编码（gbk），中文进度行会乱码；
        # EVAL_MANAGED_BY=api 让 test_suite 跳过自记历史（本管理器是唯一写者，D028）
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "EVAL_MANAGED_BY": "api",
        }
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            run.log_tail.append(f"进程启动失败：{exc!r}")
            await self._finish(run, return_code=-1)
            return run

        task = asyncio.create_task(self._drive(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await save_run(self._history_entry(run))  # T052：running 行先行落库
        log.info("eval_run_started", run_id=run_id, dataset=params.dataset, variant=params.variant)
        return run

    async def shutdown(self) -> None:
        """服务下线时终止活跃评测子进程（避免孤儿进程跑满 40 分钟）。"""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            await proc.wait()

    async def _drive(self, run: EvalRun) -> None:
        """消费子进程 stdout：逐行进日志环形缓冲，匹配进度行更新计数。"""
        proc = self._proc
        if proc is None or proc.stdout is None:  # pragma: no cover - start_run 已保证
            await self._finish(run, return_code=-1)
            return
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            run.log_tail.append(line)
            m = _PROGRESS_RE.match(line)
            if m:
                run.current, run.total, verdict = int(m[1]), int(m[2]), m[3]
                if verdict == "PASS":
                    run.passed += 1
                else:
                    run.failed += 1
        code = await proc.wait()
        report = self.reports_dir / f"ui_{run.run_id}.json"
        await self._finish(
            run,
            return_code=code,
            report_name=report.name if code == 0 and report.exists() else None,
        )

    async def _finish(self, run: EvalRun, return_code: int, report_name: str | None = None) -> None:
        run.status = "completed" if return_code == 0 else "failed"
        run.return_code = return_code
        run.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        run.report_name = report_name
        if self._active_id == run.run_id:
            self._active_id = None
        log.info("eval_run_finished", run_id=run.run_id, status=run.status, code=return_code)
        await save_run(self._history_entry(run))  # T052：终态落库

    def _history_entry(self, run: EvalRun) -> dict[str, Any]:
        """运行记录 → eval_runs 行（summary 快照取自报告文件，缺失为 None）。"""
        summary: dict[str, Any] | None = None
        if run.report_name:
            try:
                data = json.loads((self.reports_dir / run.report_name).read_text(encoding="utf-8"))
                summary = data.get("summary") if isinstance(data.get("summary"), dict) else None
            except (OSError, json.JSONDecodeError):
                summary = None
        return {
            "run_id": run.run_id,
            "source": "ui",
            "dataset": run.params.dataset,
            "variant": run.params.variant,
            "category": run.params.category,
            "run_limit": run.params.limit,
            "status": run.status,
            "return_code": run.return_code,
            "git_sha": run.git_sha,
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
            "task_completion_rate": (summary or {}).get("task_completion_rate"),
            "tool_accuracy": (summary or {}).get("tool_accuracy"),
            "report_name": run.report_name,
            "summary": summary,
            "log_tail": "\n".join(list(run.log_tail)[-50:]),
            "finished_at": dt.datetime.now() if run.status != "running" else None,
        }


_manager: EvalRunManager | None = None


def get_eval_runner() -> EvalRunManager:
    """进程级单例（API 路由共用；测试用独立实例替换）。"""
    global _manager
    if _manager is None:
        _manager = EvalRunManager()
    return _manager
