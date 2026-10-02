"""进度/心跳/推送工具群（B5.2 自 runner 拆分的内聚单元）。

职能：EvolutionRunner 执行线程的进度落库（phase/progress）与心跳
（heartbeat_at）维护，并经注册的推送函数把阶段变化送到实时通道。
服务对象：api/runner.py 的 EvolutionRunner（evolution 任务唯一执行线程）。
职责边界：只做落库与推送，不做任务调度与业务判定；全部操作失败
仅记日志不中断进化主流程。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# 推送函数全局注册位（app startup 经 set_ws_push_fn 注入，shutdown 置 None）
_ws_push_fn: Optional[Callable] = None


def set_ws_push_fn(fn: Callable) -> None:
    """注册/注销推送函数（app 启动注入一次，关停置 None 断链）。"""
    global _ws_push_fn
    _ws_push_fn = fn


def _push_ws(task_id: str, payload: Dict[str, Any]) -> None:
    """尽力推送（非阻塞 fire-and-forget），失败留痕不外抛。"""
    if _ws_push_fn is not None:
        try:
            _ws_push_fn(task_id, payload)
        except Exception:
            logger.warning("ws push failed for task %s", task_id, exc_info=True)


def update_heartbeat(db_path: Path, task_id: str) -> None:
    """更新运行中任务的 heartbeat_at（存活信号；现行恢复按 status
    残留清理，心跳超时判定已退役）。"""
    from core.persistence.repository import evolution as repo
    try:
        repo.update_heartbeat(db_path, task_id)
    except Exception:
        logger.warning("heartbeat update failed for %s", task_id, exc_info=True)


def update_phase(db_path: Path, task_id: str, phase: str) -> None:
    """更新任务 current_phase 并推送 phase_changed 事件。"""
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
    """把结构化进度 dict 序列化写入任务 progress_json 列。"""
    from core.persistence.repository import evolution as repo
    try:
        repo.update_progress(db_path, task_id, json.dumps(progress, ensure_ascii=False))
    except Exception:
        logger.warning("progress update failed for %s", task_id, exc_info=True)
