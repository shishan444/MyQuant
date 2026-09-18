"""协作取消统一件（B4.2 双源消除：api/trading 双 runner 的 TaskController 归一）。

TaskStopRequested 异常与 TaskController 语义在两 runner 本就同构
（threading.Event + check_stop 抛出）；归一后两处 import 同一套。
"""
from __future__ import annotations

import threading


class TaskStopRequested(Exception):
    """Raised when a task stop is requested via TaskController."""


class TaskController:
    """Thread-safe task controller for cooperative cancellation."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def check_stop(self) -> None:
        if self._stop_event.is_set():
            raise TaskStopRequested()

    def wait(self, timeout: float) -> None:
        self._stop_event.wait(timeout)
