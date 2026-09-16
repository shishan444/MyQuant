"""evolution repository（B3d.1 迁移，B3d.0 冻结映射）。

行为等价锚点：update_task 的 batch1 D3 条件更新语义——提供
expected_statuses 且写 status 时 WHERE status IN (...) 生效行数为
0 返回 False（调用方转 409），不提供时无条件写（恢复类/失败路径
「任意残留态最后写」）；7 个状态机守护测试（test_task_state_machine.py）
为验收线。save_snapshot/save_history 的 INSERT OR REPLACE（复合主键
幂等）；list/count 的 created_at DESC 排序与过滤。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, insert, select, update

from core.persistence.orm.models import EvolutionHistory, EvolutionTask, GenerationSnapshot
from core.persistence.repository.dataset_meta import _engine_for, _now


def save_task(
    db_path: Path,
    task_id: str,
    target_score: float,
    template: str,
    symbol: str,
    timeframe: str,
    initial_dna,
    status: str = "pending",
) -> None:
    """Create a new evolution task, atomically entering ``status``.

    batch1 D4: 任务创建单次写入进入 pending（先 running 再改 pending 的
    双写序列已废弃）。
    """
    now = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(EvolutionTask).values(
            task_id=task_id, status=status, target_score=target_score,
            score_template=template, symbol=symbol, timeframe=timeframe,
            initial_dna=initial_dna.to_json(), created_at=now, updated_at=now,
        ))


def update_task(
    db_path: Path,
    task_id: str,
    status: Optional[str] = None,
    champion_dna=None,
    stop_reason: Optional[str] = None,
    expected_statuses: Optional[tuple] = None,
) -> bool:
    """Update task status, champion, or stop reason.

    batch1 D3 条件更新：提供 ``expected_statuses`` 且本次写入 status 时，
    UPDATE 仅在当前状态属于合法前态集合时生效——生效行数为 0 时返回
    False（调用方转 409），不静默无效果。不提供时保持既有无条件写。

    Returns:
        True 表示写入生效；False 表示前态不匹配被拒绝。
    """
    values: Dict[str, Any] = {"updated_at": _now()}
    if status is not None:
        values["status"] = status
    if champion_dna is not None:
        values["champion_dna"] = champion_dna.to_json()
    if stop_reason is not None:
        values["stop_reason"] = stop_reason

    stmt = update(EvolutionTask).where(EvolutionTask.task_id == task_id)
    if expected_statuses is not None and status is not None:
        stmt = stmt.where(EvolutionTask.status.in_(expected_statuses))

    engine = _engine_for(db_path)
    with engine.begin() as conn:
        result = conn.execute(stmt.values(**values))
        return result.rowcount > 0


def get_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """Get a task by ID."""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(EvolutionTask).where(EvolutionTask.task_id == task_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def save_snapshot(
    db_path: Path,
    task_id: str,
    generation: int,
    best_score: float,
    avg_score: float,
    best_dna,
    population,
    mutation_log: Optional[str] = None,
    wf_months: Optional[str] = None,
) -> None:
    """Save a generation snapshot（INSERT OR REPLACE，复合主键幂等）。"""
    pop_json = json.dumps([ind.to_dict() for ind in population])
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            insert(GenerationSnapshot).prefix_with("OR REPLACE").values(
                task_id=task_id, generation=generation, best_score=best_score,
                avg_score=avg_score, best_dna=best_dna.to_json(),
                population_json=pop_json, mutation_log=mutation_log,
                wf_months=wf_months, created_at=_now(),
            )
        )


def save_history(
    db_path: Path,
    task_id: str,
    generation: int,
    best_score: float,
    avg_score: float,
    top3_summary: str,
) -> None:
    """Save a lightweight history record（INSERT OR REPLACE）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            insert(EvolutionHistory).prefix_with("OR REPLACE").values(
                task_id=task_id, generation=generation, best_score=best_score,
                avg_score=avg_score, top3_summary=top3_summary, created_at=_now(),
            )
        )


def get_history(db_path: Path, task_id: str) -> List[Dict[str, Any]]:
    """Get all history records for a task, ordered by generation."""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(EvolutionHistory).where(EvolutionHistory.task_id == task_id)
            .order_by(EvolutionHistory.generation.asc())
        ).mappings().all()
    return [dict(r) for r in rows]


def list_all_tasks(
    db_path: Path,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List tasks, optionally filtered by status, ordered by creation time desc."""
    stmt = select(EvolutionTask)
    if status is not None:
        stmt = stmt.where(EvolutionTask.status == status)
    stmt = stmt.order_by(EvolutionTask.created_at.desc()).limit(limit).offset(offset)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def count_all_tasks(
    db_path: Path,
    status: Optional[str] = None,
) -> int:
    """Count total tasks, optionally filtered by status."""
    stmt = select(func.count()).select_from(EvolutionTask)
    if status is not None:
        stmt = stmt.where(EvolutionTask.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()
