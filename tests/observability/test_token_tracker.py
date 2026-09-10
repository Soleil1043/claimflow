"""token_tracker 单测（T117 收敛后存活面）：案件维度归集 + 环节标注上下文。"""

from __future__ import annotations

from prometheus_client import REGISTRY

from services.observability import token_tracker as tt


def _case_tokens(model: str) -> float:
    return REGISTRY.get_sample_value(
        "claimflow_case_tokens_total", {"model": model}
    ) or 0.0


def test_track_phase_restores_previous() -> None:
    """track_phase 嵌套：退出恢复上一层标注。"""
    assert tt.current_phase() == "other"
    with tt.track_phase("ocr"):
        assert tt.current_phase() == "ocr"
        with tt.track_phase("executor"):
            assert tt.current_phase() == "executor"
        assert tt.current_phase() == "ocr"
    assert tt.current_phase() == "other"


def test_record_usage_without_case_is_noop() -> None:
    """无案件上下文（脚本/测试直调）：不记指标、不抛错。"""
    before = _case_tokens("m1")
    tt.record_usage_to_tracker("m1", 10, 5)
    assert _case_tokens("m1") == before


async def test_track_case_records_case_tokens() -> None:
    """案件上下文置位：LLM 用量按案件维度记 CASE_TOKENS{model}。"""
    before = _case_tokens("deepseek")
    with tt.track_case("CASE-1"):
        tt.record_usage_to_tracker("deepseek", 100, 40)
    assert _case_tokens("deepseek") == before + 140


def test_track_case_resets_context() -> None:
    """退出案件上下文后归集停止（防泄漏到下一案件）。"""
    with tt.track_case("CASE-1"):
        pass
    before = _case_tokens("m2")
    tt.record_usage_to_tracker("m2", 7, 3)
    assert _case_tokens("m2") == before
