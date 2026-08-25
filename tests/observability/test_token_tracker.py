"""T029 token 统计与预算控制测试：归集、分环节、超预算告警、上下文隔离。"""

from __future__ import annotations

from prometheus_client import REGISTRY

from services.observability.token_tracker import (
    TurnTokenTracker,
    finish_turn_tokens,
    phase_ainvoke,
    record_usage_to_tracker,
    start_turn_tokens,
    track_phase,
)


def _counter(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ===== Tracker 本体 =====


def test_tracker_aggregation() -> None:
    """分环节分模型归集与汇总。"""
    t = TurnTokenTracker(conversation_id="c-1")
    t.add("deepseek-v4-flash", 100, 50, phase="intent")
    t.add("deepseek-v4-flash", 200, 80, phase="planner")
    t.add("deepseek-v4-flash", 300, 120, phase="executor")

    assert t.prompt_tokens == 600
    assert t.completion_tokens == 250
    assert t.total_tokens == 850
    assert t.phase_tokens("intent") == 150
    assert t.phase_tokens("generator") == 0

    d = t.to_dict()
    assert d["total"] == 850
    assert d["phases"]["planner"]["deepseek-v4-flash"] == {"prompt": 200, "completion": 80}


def test_tracker_same_phase_accumulates() -> None:
    """同环节多次调用累计而非覆盖。"""
    t = TurnTokenTracker()
    t.add("m", 10, 5, phase="executor")
    t.add("m", 20, 15, phase="executor")
    assert t.usage["executor"]["m"] == [30, 20]


# ===== 上下文管理与归集回调 =====


def test_record_usage_to_tracker_with_context() -> None:
    """tracker 绑定上下文后，record_usage_to_tracker 按当前环节归集。"""
    tracker = start_turn_tokens("conv-1")
    with track_phase("intent"):
        record_usage_to_tracker("m1", 100, 40)
    with track_phase("generator"):
        record_usage_to_tracker("m1", 60, 30)
    assert tracker.usage == {"intent": {"m1": [100, 40]}, "generator": {"m1": [60, 30]}}
    finish_turn_tokens(tracker)


def test_record_usage_without_tracker_is_noop() -> None:
    """无 tracker 上下文（脚本直调等）时不报错。"""
    record_usage_to_tracker("m", 1, 1)  # 不应抛错


def test_track_phase_restores_previous() -> None:
    """嵌套 track_phase 退出后恢复外层环节。"""
    tracker = start_turn_tokens("c")
    with track_phase("planner"):
        with track_phase("executor"):
            record_usage_to_tracker("m", 1, 1)
        record_usage_to_tracker("m", 2, 2)  # 应回到 planner
    assert set(tracker.usage) == {"executor", "planner"}
    finish_turn_tokens(tracker)


# ===== finish：预算告警与指标 =====


def test_finish_turn_tokens_budget_exceeded_warning(caplog, monkeypatch) -> None:
    """超预算时输出 warning 日志（含预算值），不抛错。

    monkeypatch 到 token_tracker 模块引用的 settings（跨实例陷阱，见 T028 记录）。
    """
    import services.observability.token_tracker as tt

    monkeypatch.setattr(tt.settings, "turn_token_budget", 100)

    tracker = start_turn_tokens("conv-budget")
    with track_phase("executor"):
        record_usage_to_tracker("m", 500, 500)  # 总 1000

    with caplog.at_level("WARNING", logger="services.observability.token_tracker"):
        usage = finish_turn_tokens(tracker)

    assert usage["total"] == 1000
    assert any("turn_token_budget_exceeded" in r for r in caplog.messages)


def test_finish_turn_tokens_normal_summary(caplog) -> None:
    """未超预算时输出 info 汇总日志。"""
    tracker = start_turn_tokens("conv-normal")
    with track_phase("intent"):
        record_usage_to_tracker("m", 10, 5)

    with caplog.at_level("INFO", logger="services.observability.token_tracker"):
        usage = finish_turn_tokens(tracker)
    assert usage["total"] == 15
    assert any("turn_tokens_summary" in r for r in caplog.messages)


def test_finish_turn_tokens_prometheus_metric() -> None:
    """分环节 token 入 Prometheus 指标。"""
    tracker = start_turn_tokens("conv-metrics")
    with track_phase("planner"):
        record_usage_to_tracker("m-planner", 100, 100)
    before = _counter("claimflow_turn_tokens_total", phase="planner", model="m-planner")
    finish_turn_tokens(tracker)
    after = _counter("claimflow_turn_tokens_total", phase="planner", model="m-planner")
    assert after == before + 200.0


def test_finish_clears_context() -> None:
    """finish 后上下文清空：后续归集无操作。"""
    tracker = start_turn_tokens("c")
    finish_turn_tokens(tracker)
    record_usage_to_tracker("m", 999, 999)  # 无上下文，不应进任何 tracker
    assert tracker.total_tokens == 0


# ===== phase_ainvoke（observed_ainvoke + 环节标注组合） =====


class _FakeModel:
    """带 usage 的假模型。"""

    model_name = "fake-llm"

    async def ainvoke(self, messages: object, config: object = None) -> object:
        return type("R", (), {"content": "ok", "usage_metadata": {"input_tokens": 7, "output_tokens": 3}})()


async def test_phase_ainvoke_records_to_tracker() -> None:
    """phase_ainvoke 在埋点的同时把 usage 归集到当前 tracker。"""
    tracker = start_turn_tokens("conv-pa")
    await phase_ainvoke(_FakeModel(), [], phase="compliance")  # type: ignore[arg-type]
    assert tracker.usage == {"compliance": {"fake-llm": [7, 3]}}
    finish_turn_tokens(tracker)
