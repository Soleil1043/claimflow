"""短期记忆服务：LangGraph Checkpoint 工厂 + 会话消息窗口（F14，D005/D009）。

- auto（默认）：dev profile=InMemorySaver（内存，进程生命周期）/ prod=AsyncPostgresSaver
- sqlite（T155 显式配置，dev 多实例）：AsyncSqliteSaver 共享 checkpoint 文件
  （WAL + busy_timeout 多写并发）——各实例内存隔离时他实例 RESUME 找不到
  checkpoint，版本门卫会误判降级全新重跑（T145 实锤，D071）
- prod 的 AsyncPostgresSaver.from_conn_string 返回 async context manager，
  本模块用 CheckpointManager 持有它并在应用 lifespan 内管理进出（T012 组装图时接入）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_VALID_BACKENDS = {"auto", "memory", "sqlite", "postgres"}


class CheckpointManager:
    """Checkpointer 持有者：应用 lifespan 内 start/close，全局取用。

    用法（T012 主图组装时）：
        manager = get_checkpoint_manager()
        await manager.start()            # lifespan 启动时
        graph = builder.compile(checkpointer=manager.checkpointer)
        await manager.close()            # lifespan 关停时
    """

    def __init__(self) -> None:
        self._checkpointer: BaseCheckpointSaver | None = None
        self._cm: object | None = None  # prod 下 from_conn_string 的上下文管理器
        self._sqlite_conn: object | None = None  # sqlite 后端的 aiosqlite 连接（T155）

    async def start(self) -> BaseCheckpointSaver:
        """初始化 checkpointer（幂等）。"""
        if self._checkpointer is not None:
            return self._checkpointer

        backend = settings.checkpoint_backend
        if backend not in _VALID_BACKENDS:
            msg = f"checkpoint_backend 非法：{backend}（合法值 {sorted(_VALID_BACKENDS)}）"
            raise ValueError(msg)

        use_sqlite = backend == "sqlite"
        use_memory = backend == "memory" or (backend == "auto" and not settings.is_prod)
        if use_memory:
            self._checkpointer = InMemorySaver()
            log.info("checkpointer_initialized", mode="memory")
            return self._checkpointer

        if use_sqlite:
            # 局部导入：aiosqlite/saver 只在 sqlite 后端加载
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            path = Path(settings.checkpoint_sqlite_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(path)
            # 多实例多写并发：busy 等待 + WAL 读写不互斥。注意次序——busy_timeout
            # 必须先设（WAL 切换需要短暂排他锁，双实例同时开文件时若对方正在
            # setup() DDL 会立刻 BUSY，T156 双实例 boot 实锤），且切换/建表带重试
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.commit()
            await self._retry_busy(conn, "PRAGMA journal_mode=WAL")
            await conn.commit()
            checkpointer = AsyncSqliteSaver(conn)
            # setup() 建表幂等，双实例并发开文件时可能撞锁——同样重试
            await self._retry_call(checkpointer.setup)
            self._sqlite_conn = conn
            self._checkpointer = checkpointer
            log.info("checkpointer_initialized", mode="sqlite", path=str(path))
            return self._checkpointer

        # 局部导入：psycopg 二进制依赖只在 prod 路径加载
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        cm = AsyncPostgresSaver.from_conn_string(settings.checkpoint_conn_string)
        checkpointer = await cm.__aenter__()
        # 首次使用需建表（幂等，官方约定）
        await checkpointer.setup()
        self._cm = cm
        self._checkpointer = checkpointer
        log.info("checkpointer_initialized", mode="postgres")
        return self._checkpointer

    @staticmethod
    async def _retry_busy(conn: Any, sql: str, *, attempts: int = 8, delay_s: float = 0.5) -> None:
        """执行 PRAGMA，database is locked 时退避重试（双实例同时开文件的启动窗口）。"""
        import aiosqlite

        for attempt in range(attempts):
            try:
                await conn.execute(sql)
                return
            except aiosqlite.OperationalError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(delay_s)

    @staticmethod
    async def _retry_call(func: Any, *, attempts: int = 8) -> None:
        """执行无参可调用（协程工厂），OperationalError 撞锁时退避重试。"""
        import aiosqlite

        for attempt in range(attempts):
            try:
                await func()
                return
            except aiosqlite.OperationalError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.5)

    @property
    def checkpointer(self) -> BaseCheckpointSaver:
        """当前 checkpointer（未 start 时抛错，防止静默降级）。"""
        if self._checkpointer is None:
            msg = "CheckpointManager 未初始化，请先调用 start()"
            raise RuntimeError(msg)
        return self._checkpointer

    async def close(self) -> None:
        """释放资源（幂等）。"""
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)  # type: ignore[attr-defined]
            self._cm = None
        if self._sqlite_conn is not None:
            await self._sqlite_conn.close()  # type: ignore[attr-defined]
            self._sqlite_conn = None
        self._checkpointer = None


_manager: CheckpointManager | None = None


def get_checkpoint_manager() -> CheckpointManager:
    """全局 CheckpointManager 单例。"""
    global _manager
    if _manager is None:
        _manager = CheckpointManager()
    return _manager
