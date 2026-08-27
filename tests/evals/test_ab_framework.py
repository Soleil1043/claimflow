"""A/B 实验框架测试（T040）：变体注册表 / z 检验 / 组间对比纯函数。

apply_variant 会改共享 settings 单例与 prompts 模块——测试用 try/finally 严格还原。
"""

from __future__ import annotations

from typing import Any

import pytest

import services.llm.prompts as prompts_module
from app.core.config import settings
from evals.ab_test import _token_delta, compare_reports, snapshot_llm_tokens
from evals.metrics import EvalReport, two_proportion_z_test
from evals.variants import VARIANTS, VariantSpec, apply_variant

# ---------- 双比例 z 检验 ----------


def test_z_test_significant_difference() -> None:
    """90% vs 60%（n=100）：差异显著。"""
    result = two_proportion_z_test(90, 100, 60, 100)
    assert result["significant_p05"] is True
    assert result["z"] > 1.96


def test_z_test_not_significant() -> None:
    """51% vs 49%（n=100）：差异不显著。"""
    result = two_proportion_z_test(51, 100, 49, 100)
    assert result["significant_p05"] is False
    assert abs(result["z"]) < 1.96


def test_z_test_zero_variance() -> None:
    """两组比例相同（全对）：池化方差 0 分支，不显著。"""
    result = two_proportion_z_test(100, 100, 100, 100)
    assert result["significant_p05"] is False
    assert result["z"] == 0.0


def test_z_test_zero_sample() -> None:
    """样本为 0：保守返回不显著。"""
    result = two_proportion_z_test(10, 0, 5, 10)
    assert result["significant_p05"] is False


def test_z_test_direction() -> None:
    """方向：对照组更高时 z 为负。"""
    result = two_proportion_z_test(60, 100, 90, 100)
    assert result["z"] < -1.96


# ---------- 变体注册表 ----------


def test_registry_contains_key_variants() -> None:
    """注册表含基线/图谱对比/跨供应商变体。"""
    for name in ("baseline", "hybrid", "pure_rag", "deepseek-v4-pro", "glm-5.3-flash"):
        assert name in VARIANTS
        assert VARIANTS[name].description


def test_apply_unknown_variant_raises() -> None:
    with pytest.raises(KeyError, match="未知变体"):
        apply_variant("nonexistent-variant")


def test_apply_variant_settings_override_roundtrip() -> None:
    """settings 覆盖生效并可还原（共享单例，测试必须恢复）。"""
    original = (
        settings.graph_rag_enabled,
        settings.llm_model,
        settings.llm_base_url,
        settings.llm_api_key,
    )
    try:
        apply_variant("pure_rag")
        assert settings.graph_rag_enabled is False
        apply_variant("deepseek-v4-pro")
        assert settings.llm_model == "deepseek-v4-pro"
        # baseline 现在显式固定供应商（跨供应商变体切换后可还原到 DeepSeek）
        apply_variant("baseline")
        assert settings.llm_model == "deepseek-v4-flash"
        assert settings.llm_base_url == "https://api.deepseek.com"
    finally:
        (
            settings.graph_rag_enabled,
            settings.llm_model,
            settings.llm_base_url,
            settings.llm_api_key,
        ) = original
        from services.llm.client import reset_model_cache

        reset_model_cache()
        from services.rag import graph_retriever

        graph_retriever.reset_knowledge_graph()


def test_apply_variant_env_indirection_and_empty_guard() -> None:
    """$ 字段间接引用：从 settings 解引用生效；引用空字段时拒绝（防裸奔跑评测）。"""
    original = (
        settings.llm_model,
        settings.llm_base_url,
        settings.llm_api_key,
        settings.glm_api_key,
    )
    VARIANTS["_env_test"] = VariantSpec(
        name="_env_test",
        description="测试 $ 间接引用",
        settings_overrides={"llm_model": "glm-5.3-flash", "llm_api_key": "$glm_api_key"},
    )
    try:
        settings.glm_api_key = "test-glm-key-123"
        apply_variant("_env_test")
        assert settings.llm_model == "glm-5.3-flash"
        assert settings.llm_api_key == "test-glm-key-123"

        settings.glm_api_key = ""  # 空引用 → 拒绝并保持未应用状态
        with pytest.raises(KeyError, match="为空"):
            apply_variant("_env_test")
    finally:
        (
            settings.llm_model,
            settings.llm_base_url,
            settings.llm_api_key,
            settings.glm_api_key,
        ) = original
        del VARIANTS["_env_test"]
        from services.llm.client import reset_model_cache

        reset_model_cache()


def test_registry_contains_cross_vendor_variant() -> None:
    """跨供应商变体（T041 用户切换：deepseek-v4-pro → glm-5.3-flash）已注册。"""
    spec = VARIANTS["glm-5.3-flash"]
    assert spec.settings_overrides["llm_model"] == "glm-5.3-flash"
    assert spec.settings_overrides["llm_api_key"] == "$glm_api_key"


def test_apply_variant_rejects_invalid_setting_field() -> None:
    """覆盖不存在的配置字段：注册时防不住（dataclass 自由 dict），apply 时拒绝。"""
    VARIANTS["_bad"] = VariantSpec(
        name="_bad", description="测试用非法变体", settings_overrides={"not_a_field": 1}
    )
    try:
        with pytest.raises(KeyError, match="不存在的配置字段"):
            apply_variant("_bad")
    finally:
        del VARIANTS["_bad"]


def test_apply_variant_prompt_override_syncs_consumer_modules() -> None:
    """prompt 覆盖：prompts 模块与绑定了该常量的节点模块同步修改并可还原。"""
    import nodes.intent as intent_module

    original = prompts_module.INTENT_CLASSIFICATION_PROMPT
    marker = "【A/B 实验 prompt 覆盖标记】\n" + original
    VARIANTS["_prompt_test"] = VariantSpec(
        name="_prompt_test",
        description="测试 prompt 覆盖",
        prompt_overrides={"INTENT_CLASSIFICATION_PROMPT": marker},
    )
    try:
        apply_variant("_prompt_test")
        assert prompts_module.INTENT_CLASSIFICATION_PROMPT == marker
        # 消费方模块（from ... import 绑定的字符串快照）也被同步
        assert intent_module.INTENT_CLASSIFICATION_PROMPT == marker
    finally:
        prompts_module.INTENT_CLASSIFICATION_PROMPT = original
        intent_module.INTENT_CLASSIFICATION_PROMPT = original
        del VARIANTS["_prompt_test"]


# ---------- 组间对比与 token 差分 ----------


def _make_run(name: str, passed: int, total: int, tool_p: int, tool_t: int) -> dict[str, Any]:
    report = EvalReport(
        total=total,
        passed=passed,
        task_completion_rate=passed / total,
        tool_accuracy=tool_p / tool_t if tool_t else 1.0,
        tool_scored_passed=tool_p,
        tool_scored_total=tool_t,
        compliance_pass_rate=1.0,
        human_precision=0.0,
        avg_duration_s=10.0 if name == "baseline" else 12.5,
    )
    spec = VARIANTS[name]
    return {"spec": spec, "report": report, "tokens": {"m": 1000 if name == "baseline" else 3000}}


def test_compare_reports_structure() -> None:
    """对比结构：率差异（百分点）、z 检验、耗时差、token 差。"""
    base = _make_run("baseline", 90, 100, 80, 90)
    other = _make_run("pure_rag", 60, 100, 70, 90)
    c = compare_reports(base, other)
    assert c["baseline"] == "baseline"
    assert c["variant"] == "pure_rag"
    assert c["task_completion"]["delta_pp"] == -30.0
    assert c["task_completion"]["significant_p05"] is True
    assert c["tool_accuracy"]["delta_pp"] == pytest.approx(-11.1, abs=0.15)
    assert c["avg_duration_s"]["delta_s"] == 2.5
    assert c["llm_tokens"]["total_baseline"] == 1000
    assert c["llm_tokens"]["total_variant"] == 3000


def test_token_delta_filters_and_diffs() -> None:
    """token 快照差分：只保留增量模型。"""
    before = {"model-a": 500}
    after = {"model-a": 800, "model-b": 200}
    assert _token_delta(before, after) == {"model-a": 300, "model-b": 200}
    assert _token_delta(before, {"model-a": 500}) == {}


def test_snapshot_llm_tokens_reads_counter() -> None:
    """快照精确读取 token 计数（防 _created 时间戳样本混入的实测坑）。"""
    import uuid

    from services.observability import metrics

    model = f"snapshot-test-{uuid.uuid4().hex[:6]}"  # 全新 label，计数从零开始
    metrics.record_llm_call(model, "success", 0.1, prompt_tokens=10, completion_tokens=5)
    snap = snapshot_llm_tokens()
    # 恰好 15：若把 _created（Unix 时间戳 ≈1.75e9）算入会得到天文数字
    assert snap[model] == 15


def test_apply_variant_self_reference_allows_empty() -> None:
    """$ 自引用（还原语义）允许空值：CI 无 .env 时 llm_api_key="" 是合法状态（实测 CI 坑）。"""
    original = (settings.llm_model, settings.llm_base_url, settings.llm_api_key)
    VARIANTS["_self_ref"] = VariantSpec(
        name="_self_ref",
        description="测试自引用",
        settings_overrides={
            "llm_model": "glm-5.3-flash",
            "llm_api_key": "$llm_api_key",  # 自引用：把当前值（可能为空）写回自身
        },
    )
    try:
        settings.llm_api_key = ""  # CI 场景：无 .env
        apply_variant("_self_ref")  # 不应因空值被拒
        assert settings.llm_model == "glm-5.3-flash"
        assert settings.llm_api_key == ""
    finally:
        settings.llm_model, settings.llm_base_url, settings.llm_api_key = original
        del VARIANTS["_self_ref"]
        from services.llm.client import reset_model_cache

        reset_model_cache()
