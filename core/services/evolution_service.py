"""进化域状态迁移用例（B6.2 service 用例化，路由薄化）。

用例拥有「读取-判定-写入」序列；D3 前态守护语义不变（expected_statuses
条件更新，拒绝时抛业务异常→路由转 409/404/400）。事务形态：写侧沿用
repository 函数级事务（单条件写，与事务裁决一致）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from core.persistence.db import get_task, update_task


class TaskNotFound(Exception):
    """任务不存在（路由转 404）。"""


class IllegalTransition(Exception):
    """非法状态迁移（路由转 409/400，detail 业务字符串）。"""


@dataclass
class TransitionResult:
    task_row: Dict[str, Any]


def _get_or_404(db_path: Path, task_id: str) -> Dict[str, Any]:
    row = get_task(db_path, task_id)
    if row is None:
        raise TaskNotFound(task_id)
    return row


def pause_task(db_path: Path, task_id: str) -> TransitionResult:
    """暂停运行中任务（仅 running 合法前态——batch1 D3 守护）。"""
    _get_or_404(db_path, task_id)
    applied = update_task(db_path, task_id, status="paused",
                          expected_statuses=("running",))
    if not applied:
        current = get_task(db_path, task_id)
        raise IllegalTransition(
            f"Task is not running (current: {current['status'] if current else 'missing'})"
        )
    return TransitionResult(task_row=get_task(db_path, task_id))


def stop_task(db_path: Path, task_id: str) -> TransitionResult:
    """停止任务：先经 TaskController 即时信号，再 D3 守护写入。

    终态（completed/error）不可停止——防止覆写自然完成的结果。
    """
    _get_or_404(db_path, task_id)

    # Direct signal via TaskController (instant response, no DB polling delay)
    from api.runner import get_active_controllers
    controller = get_active_controllers().get(task_id)
    if controller is not None:
        controller.request_stop()

    applied = update_task(
        db_path, task_id, status="stopped", stop_reason="user_stop",
        expected_statuses=("running", "paused", "pending"),
    )
    if not applied:
        current = get_task(db_path, task_id)
        raise IllegalTransition(
            f"Task already finished (current: {current['status'] if current else 'missing'})"
        )
    return TransitionResult(task_row=get_task(db_path, task_id))


def resume_task(db_path: Path, task_id: str) -> TransitionResult:
    """恢复暂停任务为 pending（仅 paused 合法——runner 重新拾取）。"""
    row = _get_or_404(db_path, task_id)
    if row["status"] != "paused":
        raise IllegalTransition("Task is not paused")
    update_task(db_path, task_id, status="pending")
    return TransitionResult(task_row=get_task(db_path, task_id))
