from contextvars import ContextVar
from typing import Optional


_session_dir_ctx: ContextVar[Optional[str]] = ContextVar("session_dir", default=None)

_thread_id_ctx: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)


def set_session_context(path: str):
    """
    设置当前请求链路的会话目录。
    """
    return _session_dir_ctx.set(path)


def get_session_context() -> Optional[str]:
    """获取当前请求链路的会话目录。"""
    return _session_dir_ctx.get()


def set_thread_context(thread_id: str):
    """设置当前请求链路的 Thread ID。"""
    return _thread_id_ctx.set(thread_id)


def get_thread_context() -> Optional[str]:
    """获取当前请求链路的 Thread ID。"""
    return _thread_id_ctx.get()


def reset_session_context(session_token, thread_token=None):
    """清理上下文"""
    if session_token is not None:
        _session_dir_ctx.reset(session_token)
    if thread_token is not None:
        _thread_id_ctx.reset(thread_token)
