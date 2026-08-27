"""实验变体注册表与应用层（T040，A/B 框架）。

变体 = 一组可生效的配置覆盖：
- settings_overrides：app.core.config.settings 属性覆盖（模型名 / 温度类参数 /
  graph_rag_enabled 等 pydantic-settings 字段）
- prompt_overrides：services.llm.prompts 模块常量覆盖（prompt 路径切换）

apply_variant 负责把覆盖真正生效，并处理三个已知缓存陷阱：
1. llm_model 变化 → get_chat_model 是 @cache 单例，必须 reset_model_cache
2. graph_rag_enabled 变化 → graph_retriever 惰性单例持有旧开关，必须 reset
3. prompt 常量被节点 `from services.llm.prompts import X` 绑定为字符串快照——
   除 setattr prompts 模块外，还需同步覆盖到已加载的项目子模块
   （prompt 常量名高度独特，跨模块同名误伤概率可忽略）

注册表同时服务 evals/test_suite.py 的 --variant 参数（hybrid / pure_rag 语义
与 T033 保持一致）与 evals/ab_test.py 的多变体分流。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class VariantSpec:
    """一个实验变体的完整定义。

    settings_overrides 的值支持 `"$字段名"` 间接引用——apply 时从 settings 单例
    读该字段当前值（跨供应商变体经此引用 glm_api_key 等独立配置，密钥不进代码）。
    """

    name: str
    description: str
    # settings 单例上的属性覆盖（字段名必须真实存在，防拼写错误）
    settings_overrides: dict[str, Any] = field(default_factory=dict)
    # prompts 模块常量覆盖（属性名 → 新模板文本）
    prompt_overrides: dict[str, str] = field(default_factory=dict)


VARIANTS: dict[str, VariantSpec] = {
    # T027 基线口径：flash + 混合召回
    "baseline": VariantSpec(
        name="baseline",
        description="基线：deepseek-v4-flash + 图谱混合召回（T027 基线口径）",
        settings_overrides={
            "llm_model": "deepseek-v4-flash",
            "llm_base_url": "https://api.deepseek.com",
            "llm_api_key": "$llm_api_key",  # 还原为 .env 配置的 DeepSeek Key
            "graph_rag_enabled": True,
        },
    ),
    # T033 图谱对比语义（与历史 --variant 参数保持兼容）
    "hybrid": VariantSpec(
        name="hybrid",
        description="混合召回（GraphRAG 开启）",
        settings_overrides={"graph_rag_enabled": True},
    ),
    "pure_rag": VariantSpec(
        name="pure_rag",
        description="纯 RAG 基线（GraphRAG 关闭，行为与 T031 引入前一致）",
        settings_overrides={"graph_rag_enabled": False},
    ),
    # T041 实战实验变体
    "deepseek-v4-pro": VariantSpec(
        name="deepseek-v4-pro",
        description="deepseek-v4-pro（推理更强，成本约 Flash 3 倍）",
        settings_overrides={"llm_model": "deepseek-v4-pro"},
    ),
    "glm-5.3-flash": VariantSpec(
        name="glm-5.3-flash",
        description="glm-5.3-flash（智谱，跨供应商对比，OpenAI 兼容接口）",
        settings_overrides={
            "llm_model": "glm-5.3-flash",
            "llm_base_url": "$glm_api_base_url",
            "llm_api_key": "$glm_api_key",
        },
    ),
}


def apply_variant(name: str) -> VariantSpec:
    """把变体配置覆盖真正生效（供 test_suite / ab_test 在构建图前调用）。

    直接改 settings 单例属性（评测进程生命周期内持久，直到下一个变体覆盖）；
    按覆盖内容联动重置模型缓存与图谱单例。`$字段名` 值从 settings 解引用。
    """
    spec = VARIANTS.get(name)
    if spec is None:
        available = ", ".join(sorted(VARIANTS))
        msg = f"未知变体 {name}（可用：{available}）"
        raise KeyError(msg)

    if spec.settings_overrides:
        valid_fields = type(settings).model_fields
        for key, raw in spec.settings_overrides.items():
            if key not in valid_fields:
                msg = f"变体 {name} 覆盖了不存在的配置字段：{key}"
                raise KeyError(msg)
            if isinstance(raw, str) and raw.startswith("$"):
                source_field = raw[1:]
                value = getattr(settings, source_field)
                # 空引用守卫只拦"取了别的空字段"（如 $glm_api_key 未配置）；
                # 自引用（$llm_api_key → llm_api_key，还原语义）允许空——CI 无 .env
                # 的合法环境里 api_key 就是空串，不应拒绝（实测 CI 失败根因）
                if source_field != key and not value:
                    msg = f"变体 {name} 间接引用的配置字段 {source_field} 为空（请在 .env 配置后重试）"
                    raise KeyError(msg)
            else:
                value = raw
            setattr(settings, key, value)

    if spec.prompt_overrides:
        import services.llm.prompts as prompts_module

        for key, value in spec.prompt_overrides.items():
            if not hasattr(prompts_module, key):
                msg = f"变体 {name} 覆盖了不存在的 prompt 常量：{key}"
                raise KeyError(msg)
            _apply_prompt_override(key, value)

    # 缓存联动重置（实测陷阱见模块 docstring）：
    # 模型/供应商（base_url/key）任一变化都使 ChatOpenAI 缓存失效
    provider_changed = bool(
        {"llm_model", "llm_base_url", "llm_api_key"} & set(spec.settings_overrides)
    )
    if provider_changed:
        from services.llm.client import reset_model_cache

        reset_model_cache()
    if "graph_rag_enabled" in spec.settings_overrides:
        from services.rag import graph_retriever

        graph_retriever.reset_knowledge_graph()

    log.info("variant_applied", variant=name, settings=spec.settings_overrides)
    return spec


# prompt 覆盖需要同步的已加载模块前缀（节点/Agent/服务/工具）
_SYNCED_MODULE_PREFIXES = ("nodes.", "agents.", "services.", "tools.", "workflows.")


def _apply_prompt_override(key: str, value: str) -> None:
    """prompt 切换：prompts 模块 + 所有绑定了该常量快照的项目子模块同步覆盖。"""
    import sys

    import services.llm.prompts as prompts_module

    setattr(prompts_module, key, value)
    synced = 0
    for mod_name, mod in list(sys.modules.items()):
        if mod is None or not mod_name.startswith(_SYNCED_MODULE_PREFIXES):
            continue
        if hasattr(mod, key):
            setattr(mod, key, value)
            synced += 1
    log.info("prompt_override_applied", key=key, synced_modules=synced)
