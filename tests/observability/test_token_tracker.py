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


# ---------- 单次调用只归集一次（T220/I19） ----------


def _usage_model() -> object:
    """与生产同构的假模型：构造期挂 UsageRecordingHandler（同 services.llm.client），
    带 model_name 属性（走 observed_ainvoke 非结构化分支），响应带 usage_metadata。"""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class UsageFakeModel(GenericFakeChatModel):
        model_name: str = "fake-usage-model"

    reply = AIMessage(
        content="叙述正文",
        usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
    )
    return UsageFakeModel(messages=iter([reply]), callbacks=[tt.UsageRecordingHandler()])


async def test_observed_ainvoke_counts_case_tokens_once() -> None:
    """模型级回调已归集本次用量；observed_ainvoke 非结构化分支不得再记一次。

    旧实现两条路径都归集，决定书叙述、材料提取等裸模型调用的案件 token 虚高 2 倍。
    """
    from langchain_core.messages import HumanMessage

    acc_tokens: list[int] = []
    with tt.track_case("CASE-T220") as acc:
        await tt.phase_ainvoke(_usage_model(), [HumanMessage(content="写叙述")],
                                 phase="decision_writer")
        acc_tokens.append(acc.tokens)
        acc_tokens.append(acc.calls)
    assert acc_tokens == [140, 1]
