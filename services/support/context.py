"""客服会话作用域（T202，D088）：把当前会话 id 传给客服 Agent 内的工具。

case_status_query 需按"本会话已授权案件"做属主断言，但工具入参只有 LLM 填写
的 case_id——会话身份不能交给 LLM。reply/reply_stream 在 Agent 执行期间设置
本 contextvar，工具读取；langgraph 内部任务创建时复制上下文，取值可见（与
services.observability.token_tracker.track_case 同一传播机制）。

不经 RunnableConfig：GuardedTool 守卫层不透传 config，改共享守卫层影响面更大。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current_conversation_id: ContextVar[str | None] = ContextVar(
    "claimflow_support_conversation_id", default=None
)


@contextmanager
def conversation_scope(conversation_id: str) -> Iterator[None]:
    """在作用域内把当前客服会话 id 暴露给工具层。"""
    token = _current_conversation_id.set(conversation_id)
    try:
        yield
    finally:
        try:
            _current_conversation_id.reset(token)
        except ValueError:
            # 流式路径为 async generator：客户端断连时 aclose 可能在另一 Context
            # 触发，reset 拒绝跨 Context 令牌——退化为显式清空
            _current_conversation_id.set(None)


def current_conversation_id() -> str | None:
    """当前客服会话 id；客服 Agent 作用域外为 None（工具据此 fail-closed）。"""
    return _current_conversation_id.get()
