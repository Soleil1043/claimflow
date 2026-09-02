"""评测台 UI 回调测试（T053 修复回归）：poll 返回元组与 tick 输出组件一致。

背景实测缺陷：T053 给 poll 扩展趋势输出时两处 return 仍是 5 值（tick 声明 6 输出），
Gradio 每 2s tick 报错 → 运行日志/完成结果全部不刷新。本文件以返回元数断言锁死该类缺陷，
并覆盖运行中按钮禁用态与完成后 done 短路（state 持久化）。
"""

from __future__ import annotations

from typing import Any

import pytest

import ui.eval_app as eval_app

# timer.tick 声明的输出组件数（状态/日志/报告下拉/摘要/失败表/趋势图/state/按钮）
_EXPECTED_POLL_OUTPUTS = 8
_REPORT = {
    "dataset": "main",
    "variant": "baseline",
    "generated_at": "2026-09-02 12:00:00",
    "git_sha": "abc1234",
    "summary": {
        "total": 2,
        "passed": 1,
        "task_completion_rate": 0.5,
        "tool_accuracy": 1.0,
        "compliance_pass_rate": 1.0,
        "avg_duration_s": 1.0,
    },
    "failures": [{"case_id": "A-002", "category": "edge_case", "answer": "", "used_tools": []}],
}


class _StubClient:
    """按编排返回固定响应的评测客户端桩。"""

    def __init__(self, run: dict[str, Any]) -> None:
        self._run = run
        self.status_calls = 0
        self.reports_calls = 0
        self.trends_calls = 0

    async def status(self, run_id: str) -> dict[str, Any]:
        self.status_calls += 1
        return self._run

    async def reports(self) -> list[dict[str, Any]]:
        self.reports_calls += 1
        return [{"name": self._run.get("report_name") or "ui_x.json"}]

    async def report(self, name: str) -> dict[str, Any]:
        return _REPORT

    async def trends(self) -> dict[str, Any]:
        self.trends_calls += 1
        return {"points": []}


def _run(status: str, **extra: Any) -> dict[str, Any]:
    base = {
        "run_id": "r1",
        "status": status,
        "params": {"dataset": "main", "variant": "baseline", "limit": 2},
        "current": 2,
        "total": 2,
        "passed": 1,
        "failed": 1,
        "return_code": 0,
        "report_name": "ui_r1.json",
        "log_tail": ["[  1/2] PASS A-001", "[  2/2] FAIL A-002"],
    }
    base.update(extra)
    return base


def _btn(result: Any) -> dict[str, Any]:
    """提取元组中的按钮 update（dict 形态：{"__type__": "update", ...}）。"""
    return result[-1]


async def test_poll_running_returns_matching_arity_and_disabled_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubClient(_run("running"))
    monkeypatch.setattr(eval_app, "client", stub)
    state = {"run_id": "r1"}

    result = await eval_app.poll(state, "全部", "全部")
    assert len(result) == _EXPECTED_POLL_OUTPUTS
    status, log, _, _, _, _, state_out, btn = result
    assert "运行中" in status and "1" in status
    assert "PASS A-001" in log and "FAIL A-002" in log
    assert state_out is state
    assert btn["interactive"] is False and "运行中" in btn["value"]


async def test_poll_completed_loads_report_and_reenables_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubClient(_run("completed"))
    monkeypatch.setattr(eval_app, "client", stub)
    state = {"run_id": "r1"}

    result = await eval_app.poll(state, "全部", "全部")
    assert len(result) == _EXPECTED_POLL_OUTPUTS
    head, log, reports_dd, summary, fails, _fig, state_out, btn = result
    assert "评测完成" in head
    assert reports_dd["value"] == "ui_r1.json"
    assert "报告摘要" in summary
    assert fails and fails[0][0] == "A-002"
    assert state_out.get("done") is True  # state 随返回持久化
    assert btn["interactive"] is True and "开始评测" in btn["value"]

    # done 置位后再次 tick：短路返回空更新，不再发起网络请求
    calls_before = (stub.status_calls, stub.reports_calls, stub.trends_calls)
    idle = await eval_app.poll(state_out, "全部", "全部")
    assert len(idle) == _EXPECTED_POLL_OUTPUTS
    assert (stub.status_calls, stub.reports_calls, stub.trends_calls) == calls_before


async def test_poll_idle_without_run(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubClient(_run("running"))
    monkeypatch.setattr(eval_app, "client", stub)
    state: dict = {}
    result = await eval_app.poll(state, "全部", "全部")
    assert len(result) == _EXPECTED_POLL_OUTPUTS
    assert stub.status_calls == 0  # 无 run_id 不发请求


async def test_start_eval_button_state(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StartStub:
        async def start_run(self, payload: dict) -> dict:
            return {"run": {"run_id": "r-new"}}

    monkeypatch.setattr(eval_app, "client", _StartStub())
    status, state, btn = await eval_app.start_eval("main", "全部", 2, "baseline", True, {})
    assert state["run_id"] == "r-new"
    assert "已启动" in status
    assert btn["interactive"] is False and "运行中" in btn["value"]
