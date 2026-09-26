"""dev 共享 checkpoint 后端测试（T155，D071）：T145 场景回归 + CheckpointManager 装配。

T145 实锤场景：dev 多实例各自 InMemorySaver，实例 B 认领 RESUME 任务后
aget_state 落空 → 版本门卫误判 → 全新重跑、人工决议蒸发。本文件用两个
独立 AsyncSqliteSaver（同一文件、WAL）模拟两个实例，证明共享 checkpoint
下 resume 跨"实例"成立——这是 CHECKPOINT_BACKEND=sqlite 的存在理由。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from services.memory.short_term import CheckpointManager, get_checkpoint_manager


def _build_toy_graph(checkpointer: Any) -> Any:
    """两步玩具图：ask 节点 interrupt 挂起，resume 后写 answer 终态。"""

    def ask(state: dict) -> dict:
        answer = interrupt({"question": "补件材料齐了吗"})
        return {"answer": answer, "asked": True}

    builder = StateGraph(dict)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    return builder.compile(checkpointer=checkpointer)


async def _open_saver(path: Path) -> Any:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.commit()
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver, conn


async def test_t145_shared_sqlite_checkpoint_resume_across_instances(tmp_path: Path) -> None:
    """T145 回归：两个独立 saver（同一 sqlite 文件）= 两个实例。

    实例 A 跑到 interrupt 挂起 → 实例 B 不带任何内存状态，仅凭共享文件
    就能读到 checkpoint 并 Command(resume) 续跑到终态（而非全新重跑）。
    """
    db = tmp_path / "checkpoints.sqlite"

    saver_a, conn_a = await _open_saver(db)
    graph_a = _build_toy_graph(saver_a)
    config = {"configurable": {"thread_id": "CASE-T145-0001"}}
    result_a = await graph_a.ainvoke({"applicant": "张三"}, config=config)
    assert result_a.get("__interrupt__"), "实例 A 应在 ask 节点 interrupt 挂起"

    # 实例 B：全新进程语义——自己的 saver、自己的图对象，只共享文件
    saver_b, conn_b = await _open_saver(db)
    graph_b = _build_toy_graph(saver_b)
    snapshot = await graph_b.aget_state(config)
    assert snapshot is not None and snapshot.next, "实例 B 应能从共享文件读到挂起状态"

    result_b = await graph_b.ainvoke(Command(resume="齐了"), config=config)
    assert result_b.get("answer") == "齐了", "实例 B 应 resume 续跑而非全新重跑"

    await conn_a.close()
    await conn_b.close()


async def test_checkpoint_manager_sqlite_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CheckpointManager 装配：backend=sqlite 时返回共享文件 saver 并可正常收放。"""
    import services.memory.short_term as short_term_module

    # patch 模块视角的 settings（tests/core/test_logging reload config 后对象置换）
    db = tmp_path / "ckpt" / "checkpoints.sqlite"
    monkeypatch.setattr(short_term_module.settings, "checkpoint_backend", "sqlite")
    monkeypatch.setattr(short_term_module.settings, "checkpoint_sqlite_path", str(db))

    manager = CheckpointManager()
    saver = await manager.start()
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    assert isinstance(saver, AsyncSqliteSaver)
    assert await manager.start() is saver  # 幂等
    await manager.close()

    # 文件已建（父目录自动创建）
    assert db.exists()


async def test_checkpoint_manager_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.memory.short_term as short_term_module

    monkeypatch.setattr(short_term_module.settings, "checkpoint_backend", "redis")
    manager = CheckpointManager()
    with pytest.raises(ValueError, match="checkpoint_backend 非法"):
        await manager.start()
    get_checkpoint_manager()  # 无副作用
