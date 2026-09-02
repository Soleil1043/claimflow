"""T051/T052 评测 API 测试：运行生命周期（假命令注入，不跑真实 LLM）+ 报告与历史查询。

假命令以 `python -c <script> <run_id> <reports_dir>` 模拟评测子进程：打印进度行 /
退出码 / 报告落盘均可编排；路由单例经 monkeypatch 替换为临时目录上的独立管理器；
DB 统一替换为内存 SQLite（eval_runs 历史落库验证）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.db.session as session_module
from app.main import app
from services.db.models import Base
from services.eval_history import get_git_sha
from services.eval_runner import EvalRunManager, EvalRunParams

# 成功脚本：两行进度（1 PASS / 1 FAIL）+ 报告落盘 ui_<run_id>.json
_OK_SCRIPT = """
import json, sys
from pathlib import Path
print("[  1/2] PASS A-001 阑尾炎手术", flush=True)
print("[  2/2] FAIL A-002 等待期", flush=True)
Path(sys.argv[2]).joinpath("ui_" + sys.argv[1] + ".json").write_text(json.dumps({
    "dataset": "main", "variant": "baseline", "generated_at": "2026-09-02 12:00:00",
    "summary": {"total": 2, "passed": 1, "task_completion_rate": 0.5,
                "tool_accuracy": 1.0, "compliance_pass_rate": 1.0, "avg_duration_s": 1.0,
                "trajectory": {"order": {"rate": 1.0, "scored": 2.0}}},
    "failures": [],
}), encoding="utf-8")
"""

# 失败脚本：异常退出且不落盘
_FAIL_SCRIPT = "import sys\nprint('boom', flush=True)\nsys.exit(3)\n"

# 慢脚本：占住单活跃守卫（测 409），用后 shutdown 终止
_SLOW_SCRIPT = "import time\ntime.sleep(30)\n"


def _script_manager(tmp_path: Path, script: str) -> EvalRunManager:
    def builder(params: EvalRunParams, run_id: str, reports_dir: Path) -> list[str]:
        return [sys.executable, "-c", script, run_id, str(reports_dir)]

    return EvalRunManager(reports_dir=tmp_path, command_builder=builder)


@pytest.fixture(autouse=True)
async def _mem_db(monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker:
    """内存 SQLite 承接 eval_runs 历史写（隔离真实 DB，本文件全部测试生效）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_session_factory", factory)
    yield factory
    await engine.dispose()


def _patch_runner(monkeypatch: pytest.MonkeyPatch, manager: EvalRunManager) -> None:
    import app.api.v1.evals as evals_router

    monkeypatch.setattr(evals_router, "get_eval_runner", lambda: manager)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _wait_terminal(client: AsyncClient, run_id: str, timeout_s: float = 20.0) -> dict:
    """轮询直到运行结束（子进程启动在 Windows 上可到秒级）。"""
    status: dict = {}
    for _ in range(int(timeout_s / 0.1)):
        resp = await client.get(f"/api/v1/evals/runs/{run_id}")
        status = resp.json()
        if status["status"] != "running":
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"评测运行未在 {timeout_s}s 内结束：{status}")


async def test_run_lifecycle_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _script_manager(tmp_path, _OK_SCRIPT)
    _patch_runner(monkeypatch, manager)
    async with _client() as client:
        resp = await client.post("/api/v1/evals/runs", json={"dataset": "main", "limit": 2})
        assert resp.status_code == 200
        body = resp.json()
        run_id = body["run"]["run_id"]
        assert body["run"]["status"] == "running"
        assert body["run"]["params"]["limit"] == 2

        status = await _wait_terminal(client, run_id)
        assert status["status"] == "completed"
        assert (status["current"], status["total"]) == (2, 2)
        assert (status["passed"], status["failed"]) == (1, 1)
        assert status["report_name"] == f"ui_{run_id}.json"
        assert any("PASS A-001" in line for line in status["log_tail"])

        runs = (await client.get("/api/v1/evals/runs")).json()["runs"]
        assert any(r["run_id"] == run_id for r in runs)

        reports = (await client.get("/api/v1/evals/reports")).json()["reports"]
        assert reports and reports[0]["name"] == f"ui_{run_id}.json"
        assert reports[0]["task_completion_rate"] == 0.5
        assert reports[0]["passed"] == 1

        detail = (await client.get(f"/api/v1/evals/reports/{status['report_name']}")).json()
        assert detail["summary"]["trajectory"]["order"]["scored"] == 2.0


async def test_duplicate_start_conflict_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _script_manager(tmp_path, _SLOW_SCRIPT)
    _patch_runner(monkeypatch, manager)
    try:
        async with _client() as client:
            first = await client.post("/api/v1/evals/runs", json={"dataset": "main", "limit": 1})
            assert first.status_code == 200
            second = await client.post("/api/v1/evals/runs", json={"dataset": "main", "limit": 1})
            assert second.status_code == 409
            assert "已有评测在运行" in second.json()["detail"]
    finally:
        await manager.shutdown()  # 终止占守卫的慢子进程


async def test_failed_run_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _script_manager(tmp_path, _FAIL_SCRIPT)
    _patch_runner(monkeypatch, manager)
    async with _client() as client:
        run_id = (await client.post("/api/v1/evals/runs", json={"dataset": "main"})).json()["run"][
            "run_id"
        ]
        status = await _wait_terminal(client, run_id)
        assert status["status"] == "failed"
        assert status["return_code"] == 3
        assert status["report_name"] is None
        assert any("boom" in line for line in status["log_tail"])

        reports = (await client.get("/api/v1/evals/reports")).json()["reports"]
        assert reports == []


async def test_run_not_found_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_runner(monkeypatch, _script_manager(tmp_path, _OK_SCRIPT))
    async with _client() as client:
        resp = await client.get("/api/v1/evals/runs/does-not-exist")
        assert resp.status_code == 404


async def test_report_name_guard_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _script_manager(tmp_path, _OK_SCRIPT)
    _patch_runner(monkeypatch, manager)
    async with _client() as client:
        bad = await client.get("/api/v1/evals/reports/..%2Fsecret.json")
        assert bad.status_code in (400, 404)  # 禁止路径穿越
        invalid = await client.get("/api/v1/evals/reports/.hidden.json")
        assert invalid.status_code == 400
        missing = await client.get("/api/v1/evals/reports/no_such_report.json")
        assert missing.status_code == 404


async def test_meta_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_runner(monkeypatch, _script_manager(tmp_path, _OK_SCRIPT))
    async with _client() as client:
        resp = await client.get("/api/v1/evals/meta")
        assert resp.status_code == 200
        meta = resp.json()
        assert "main" in meta["datasets"]
        assert "simple_faq" in meta["categories"]
        assert any(v["name"] == "baseline" for v in meta["variants"])


# ===== T052：运行历史持久化 =====


async def test_history_persisted_and_visible_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """运行终态落库（git_sha/率/报告回链），换新管理器（模拟重启）后 /runs 仍可查。"""
    from services.eval_history import list_runs as list_history

    manager = _script_manager(tmp_path, _OK_SCRIPT)
    _patch_runner(monkeypatch, manager)
    async with _client() as client:
        run_id = (
            await client.post("/api/v1/evals/runs", json={"dataset": "main", "limit": 2})
        ).json()["run"]["run_id"]
        status = await _wait_terminal(client, run_id)
        assert status["status"] == "completed"
        assert status["git_sha"] not in ("", "unknown")

        # 落库断言：终态行带 summary 快照与率冗余列
        rows = await list_history(10)
        assert len(rows) == 1
        row = rows[0]
        assert row.run_id == run_id and row.source == "ui"
        assert row.status == "completed" and row.return_code == 0
        assert row.report_name == f"ui_{run_id}.json"
        assert float(row.task_completion_rate) == 0.5  # type: ignore[arg-type]
        assert row.summary is not None and row.summary["total"] == 2
        assert "PASS A-001" in (row.log_tail or "")

        # 模拟服务重启：换全新管理器（内存为空），历史仍可经 /runs 与 /runs/{id} 查询
        _patch_runner(monkeypatch, EvalRunManager(reports_dir=tmp_path))
        runs = (await client.get("/api/v1/evals/runs")).json()["runs"]
        assert any(r["run_id"] == run_id and r["status"] == "completed" for r in runs)
        detail = (await client.get(f"/api/v1/evals/runs/{run_id}")).json()
        assert detail["status"] == "completed" and detail["total"] == 2
        assert detail["task_completion_rate"] == 0.5


async def test_history_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 不可用时历史写入静默失败（fail-open），评测本身照常完成。"""
    manager = _script_manager(tmp_path, _OK_SCRIPT)
    _patch_runner(monkeypatch, manager)

    def _broken_factory() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(session_module, "get_session_factory", _broken_factory)
    async with _client() as client:
        run_id = (
            await client.post("/api/v1/evals/runs", json={"dataset": "main", "limit": 2})
        ).json()["run"]["run_id"]
        status = await _wait_terminal(client, run_id)
        assert status["status"] == "completed"  # 评测不受历史故障影响
        assert status["report_name"] == f"ui_{run_id}.json"


async def test_git_sha_helper() -> None:
    """git_sha 非空、进程内缓存稳定（git 仓库内应为真实短 SHA）。"""
    sha = get_git_sha()
    assert sha and len(sha) <= 40
    assert get_git_sha() == sha


async def test_cli_run_self_record(_mem_db: Any) -> None:
    """CLI 运行收尾自记历史（T052）：完成/失败两态都落库。"""
    from evals.metrics import CaseResult, aggregate
    from evals.schemas import EvalCategory
    from evals.test_suite import _save_cli_history
    from services.eval_history import list_runs as list_history

    report = aggregate([CaseResult(case_id="A", category=EvalCategory.MULTI_STEP, passed=True)])
    args = types.SimpleNamespace(dataset="main", category=None, limit=5, variant="baseline")
    await _save_cli_history(args, report, "")
    await _save_cli_history(args, None, "boom")
    rows = await list_history(10)
    assert [r.status for r in rows] == ["failed", "completed"]  # 新→旧
    assert all(r.source == "cli" for r in rows)
    assert rows[0].error == "boom"
    assert rows[1].task_completion_rate is not None
