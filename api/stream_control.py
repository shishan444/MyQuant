"""流式任务协作控制注册表（B8.3 流式批的取消语义承载，B8.2 先就位）。

B8.0 语义点①：SSE AbortSignal 断开不再隐含取消——socket.io 形态下
经 C→S cancel 事件（/streams namespace）协作取消。注册表映射
session_id → 取消回调；无注册（任务未在跑/已完成）时 no-op。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cancel_callbacks: dict[str, Callable[[], None]] = {}

# 流式任务并发槽位（verify/batch 两流式端点共用；本地单用户 + 浏览器
# 多页并发留裕量，满载即拒绝新流而非无界 create_task）
_STREAM_SLOTS = threading.BoundedSemaphore(4)


def acquire_stream_slot() -> bool:
    """占用一个流式任务并发槽位（非阻塞）。

    Returns:
        True=占用成功；False=并发已满（调用方应 429 拒绝）。
    """
    return _STREAM_SLOTS.acquire(blocking=False)


def release_stream_slot() -> None:
    """释放并发槽位（任务终止时；与 unregister 同点调用）。"""
    _STREAM_SLOTS.release()


def register(session_id: str, cancel_fn: Callable[[], None]) -> None:
    """注册流式任务的取消回调（任务启动时）。"""
    with _lock:
        _cancel_callbacks[session_id] = cancel_fn


def unregister(session_id: str) -> None:
    """注销（任务完成/失败时）。"""
    with _lock:
        _cancel_callbacks.pop(session_id, None)


def request_cancel(session_id: Optional[str]) -> None:
    """协作取消（C→S cancel 事件入口；未注册时 no-op）。"""
    if not session_id:
        return
    with _lock:
        fn = _cancel_callbacks.get(session_id)
    if fn is None:
        return
    try:
        fn()
    except Exception:
        logger.warning("stream cancel failed for %s", session_id, exc_info=True)
