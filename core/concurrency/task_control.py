"""协作取消统一件（B4.2 双源消除：进化/交易双 runner 的 TaskController 归一）。

TaskStopRequested 异常与 TaskController 语义在各 runner 本就同构
（threading.Event + check_stop 抛出）；归一后各处 import 同一套。
pause 信号面（research-tooling-data-quality pause 族补齐）沿 stop 同构
形制加入：进化任务 pause 经直达信号让 worker 线程当代落库后协作退出。
"""
from __future__ import annotations

import threading


class TaskStopRequested(Exception):
    """Raised when a task stop is requested via TaskController."""


class TaskPauseRequested(Exception):
    """暂停请求经 TaskController 触发时抛出（消费方：进化任务执行链）。"""


class TaskController:
    """Thread-safe task controller for cooperative cancellation."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def check_stop(self) -> None:
        if self._stop_event.is_set():
            raise TaskStopRequested()

    def request_pause(self) -> None:
        self._pause_event.set()

    @property
    def pause_requested(self) -> bool:
        return self._pause_event.is_set()

    def check_pause(self) -> None:
        """置位时抛 TaskPauseRequested（检查点由消费方定——进化链在当代
        全部落库与推送完成后检查，兑现「当前代完整落库后暂停」契约）。"""
        if self._pause_event.is_set():
            raise TaskPauseRequested()

    def wait(self, timeout: float) -> None:
        self._stop_event.wait(timeout)
