"""T051 评测 API 测试：运行生命周期（假命令注入，不跑真实 LLM）+ 报告查询接口。

假命令以 `python -c <script> <run_id> <reports_dir>` 模拟评测子进程：打印进度行 /
退出码 / 报告落盘均可编排；路由单例经 monkeypatch 替换为临时目录上的独立管理器。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
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
