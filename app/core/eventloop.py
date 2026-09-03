"""asyncio 事件循环兼容工具。

背景（生产环境实测发现）：psycopg 异步驱动不支持 Windows 默认的
ProactorEventLoop，APP_PROFILE=prod 的入口（评测运行器、宿主机直连 prod 栈的
脚本）在 Windows 上会抛 "Psycopg cannot use the 'ProactorEventLoop'"。
Linux/macOS 与 CI 不受影响（policy 仅 win32 生效）。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any


def run_async(coro: Any) -> Any:
    """asyncio.run 的 Windows 兼容包装：win32 下切 SelectorEventLoop policy。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)
