"""Embedding 事件循环不阻塞测试（T219/I18）。

BGE-M3 编码是同步 CPU 推理（100-500ms/条）。在线异步路径直接调用会让事件循环
停摆：同进程所有请求、租约心跳、其他案件交付全部连坐，守卫 asyncio.timeout 也
无法在编码期间生效。这里用 time.sleep 模拟慢编码，以"事件循环在编码期间能否继续
调度其他协程"为判据。
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

import services.rag.embedder as embedder_module
import services.rag.retriever as retriever_module
from services.rag.retriever import search_kb

_SLOW_S = 0.4


class _FakeQdrant:
    async def collection_exists(self, name: str) -> bool:
        return True

    async def query_points(self, **kwargs: Any) -> Any:
        return SimpleNamespace(points=[])


async def _ticks_during(coro: Any) -> tuple[Any, int]:
    """并发跑 coro 与一个 20ms 节拍器，返回 (coro 结果, 节拍数)。"""
    ticks = 0
    done = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    async def run() -> Any:
        try:
            return await coro
        finally:
            done.set()

    result, _ = await asyncio.gather(run(), ticker())
    return result, ticks


@pytest.fixture()
def slow_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_embed_query(text: str) -> list[float]:
        time.sleep(_SLOW_S)
        return [0.1, 0.2, 0.3, 0.4]

    monkeypatch.setattr(embedder_module, "embed_query", slow_embed_query)
    monkeypatch.setattr(retriever_module, "get_qdrant_client", lambda: _FakeQdrant())


async def test_search_kb_does_not_block_event_loop(slow_embed) -> None:
    """慢编码期间节拍器持续推进（阻塞实现下 0.4s 内节拍≈0-1）。"""
    _, ticks = await _ticks_during(search_kb("阑尾炎等待期"))
    assert ticks >= 8, f"编码期间事件循环被阻塞（节拍仅 {ticks}）"


async def test_guard_timeout_fires_during_encoding(slow_embed) -> None:
    """守卫超时在编码期间按时生效（阻塞实现下要等编码跑完才抛）。"""
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await search_kb("阑尾炎等待期")
    assert time.perf_counter() - started < _SLOW_S - 0.1


def test_cold_start_concurrent_loads_model_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """编码进线程池后冷启动可能并发首调：模型只加载一次（防重复加载约 2GB）。"""
    loads = 0

    class FakeST:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal loads
            loads += 1
            time.sleep(0.05)

    # 假模块注入 sys.modules：不导入真 sentence_transformers（torch 冷导入约 10s）
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=FakeST))
    monkeypatch.setattr(embedder_module, "_model", None)
    threads = [threading.Thread(target=embedder_module._get_model) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert loads == 1
    monkeypatch.setattr(embedder_module, "_model", None)


async def test_memory_store_slow_sync_embed_does_not_block_loop() -> None:
    """守护测试：记忆 Store（langgraph InMemoryStore）的同步嵌入函数经 aembed_* 走
    run_in_executor，不阻塞事件循环——审查 I18 对长期记忆路径的判断在
    langgraph 1.2.11 / langchain-core 1.6.1 下不成立，long_term 无需改动。
    依赖升级若改变该行为，本用例会先红。"""
    from langgraph.store.memory import InMemoryStore

    def slow_embed(texts: list[str]) -> list[list[float]]:
        time.sleep(_SLOW_S)
        return [[0.1, 0.2] for _ in texts]

    store = InMemoryStore(index={"dims": 2, "embed": slow_embed, "fields": ["t"]})
    _, put_ticks = await _ticks_during(store.aput(("u",), "k", {"t": "记忆"}))
    _, search_ticks = await _ticks_during(store.asearch(("u",), query="记忆"))
    assert put_ticks >= 8 and search_ticks >= 8
