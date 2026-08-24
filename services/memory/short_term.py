"""短期记忆服务：LangGraph Checkpoint 工厂 + 会话消息窗口（F14，D005/D009）。

- dev profile：InMemorySaver（内存，进程生命周期）
- prod profile：AsyncPostgresSaver（PostgreSQL 持久化，连接串 psycopg 协议，
  注意类名无 "QL"，见 decisions.md D009）

prod 的 AsyncPostgresSaver.from_conn_string 返回 async context manager，
本模块用 CheckpointManager 持有它并在应用 lifespan 内管理进出（T012 组装图时接入）。
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# 对话历史滑窗：注入 LLM 的最大历史条数（Token 预算控制，architecture.md 6.3）
MAX_HISTORY_MESSAGES = 20


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

    async def start(self) -> BaseCheckpointSaver:
        """初始化 checkpointer（幂等）。"""
        if self._checkpointer is not None:
            return self._checkpointer

        if not settings.is_prod:
            self._checkpointer = InMemorySaver()
            log.info("checkpointer_initialized", mode="memory")
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
        self._checkpointer = None


_manager: CheckpointManager | None = None


def get_checkpoint_manager() -> CheckpointManager:
    """全局 CheckpointManager 单例。"""
    global _manager
    if _manager is None:
        _manager = CheckpointManager()
    return _manager
