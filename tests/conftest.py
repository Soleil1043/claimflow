"""全局测试夹具。"""

from __future__ import annotations

import os

import pytest

# 测试完全离线：HuggingFace 模型一律用本地缓存，禁止联网校验/下载。
# 一旦有测试路径意外触达 RAG/Embedding 且缓存元数据校验走网络，会在无外网
# 环境下无限阻塞（T046 期间实测：意图 mock 缺口 → 意外进入 RAG → HF 挂起）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _reset_graph_caches():
    """隔离 react/worker 子图全局缓存（T047）。

    create_agent 子图编译一次即缓存（generator._react_agent / runner._worker_cache），
    各测试文件 patch 的 get_chat_model 不同——不重置会串用上一测试的假模型。
    """
    import agents.runner as runner_module
    import nodes.generator as generator_module

    generator_module._react_agent = None
    runner_module._worker_cache.clear()
    yield
    generator_module._react_agent = None
    runner_module._worker_cache.clear()


@pytest.fixture(autouse=True)
def _memory_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关闭长期记忆写路径（T034）。

    A06 场景测试中 REJECT 终态会 force 触发记忆写入，若不关闭会真实加载
    BGE-M3（约 2GB）并写 ./data/qdrant；tests/memory/ 的专项测试自行显式开启。

    注意 settings 双实例陷阱（见 progress.md T028/T029）：patch 打在
    long_term 模块实际引用的 settings 对象上，保证对消费方生效。
    """
    import services.memory.long_term as long_term_module

    monkeypatch.setattr(long_term_module.settings, "memory_enabled", False)
