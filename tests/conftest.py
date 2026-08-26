"""全局测试夹具。"""

from __future__ import annotations

import pytest


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
