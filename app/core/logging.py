"""structlog 结构化日志配置。

统一输出 JSON 格式日志（dev 控制台带缩进便于阅读，prod 紧凑单行便于采集），
所有模块通过 `from app.core.logging import get_logger` 获取 logger，
禁止 print（AGENTS.md 4.1 约定）。
"""

import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """初始化 structlog 与标准库日志集成，应用启动时调用一次。"""
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # dev：控制台彩色/缩进可读输出；prod：紧凑 JSON 单行（供日志采集）
    renderer: structlog.typing.Processor
    if settings.is_prod:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    # 收敛第三方库的噪音日志
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """获取结构化 logger。

    用法：
        log = get_logger(__name__)
        log.info("tool_executed", tool="policy_query", duration_ms=120)
    """
    return structlog.stdlib.get_logger(name)
