"""
按会话统计检索工具的调用次数。
"""
import os
import threading
from typing import Optional, Tuple
from api.context import get_thread_context


_lock = threading.Lock()
_counts = {}

_GLOBAL_KEY = "__global__"


def _limit(default_limit: int) -> int:
    try:
        return max(1, int(os.getenv("SEARCH_BUDGET_PER_TURN", str(default_limit))))
    except (TypeError, ValueError):
        return default_limit


def consume(kind: str, default_limit: int) -> Tuple[bool, int, int]:
    """消耗一次检索预算"""
    limit = _limit(default_limit)
    thread_id = get_thread_context() or _GLOBAL_KEY
    key = (thread_id, kind)
    with _lock:
        used = _counts.get(key, 0) + 1
        _counts[key] = used
    return used <= limit, used, limit


def remaining(kind: str, default_limit: int) -> int:
    """计算剩余预算"""
    limit = _limit(default_limit)
    thread_id = get_thread_context() or _GLOBAL_KEY
    with _lock:
        used = _counts.get((thread_id, kind), 0)
    return max(0, limit - used)


def reset(thread_id: Optional[str] = None) -> None:
    """刷新次数"""
    with _lock:
        if thread_id is None:
            _counts.clear()
            return
        for key in [k for k in _counts if k[0] == thread_id]:
            _counts.pop(key, None)
