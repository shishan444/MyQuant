"""进度/心跳/WS 推送工具群（B5.2 自 runner 拆分的内聚单元）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Global reference to the FastAPI app's asyncio loop for WS push
_ws_push_fn: Optional[Callable] = None


def set_ws_push_fn(fn: Callable) -> None:
    """Register a function that the runner can call to push WS messages.

    Called once during app startup.
    """
    global _ws_push_fn
    _ws_push_fn = fn


def _push_ws(task_id: str, payload: Dict[str, Any]) -> None:
    """Fire-and-forget WS push (non-blocking) with observability."""
    if _ws_push_fn is not None:
        try:
            _ws_push_fn(task_id, payload)
        except Exception:
            logger.warning("ws push failed for task %s", task_id, exc_info=True)


def update_heartbeat(db_path: Path, task_id: str) -> None:
    """Update heartbeat_at timestamp for a running task."""
    from core.persistence.repository import evolution as repo
    try:
        repo.update_heartbeat(db_path, task_id)
    except Exception:
        logger.warning("heartbeat update failed for %s", task_id, exc_info=True)


def update_phase(db_path: Path, task_id: str, phase: str) -> None:
    """Update current_phase for a task and push WS notification."""
    from core.persistence.repository import evolution as repo
    try:
        repo.update_phase(db_path, task_id, phase)
    except Exception:
        logger.warning("phase update failed for %s", task_id, exc_info=True)
    _push_ws(task_id, {
        "type": "phase_changed",
        "task_id": task_id,
        "phase": phase,
    })


def update_progress(db_path: Path, task_id: str, progress: dict) -> None:
    """Write structured progress JSON to DB."""
    from core.persistence.repository import evolution as repo
    try:
        repo.update_progress(db_path, task_id, json.dumps(progress, ensure_ascii=False))
    except Exception:
        logger.warning("progress update failed for %s", task_id, exc_info=True)
