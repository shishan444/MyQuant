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


# ---------------------------------------------------------------------------
# B3d.2 收口：runner/路由原裸 SQL 的等价迁移
# ---------------------------------------------------------------------------

def update_phase(db_path: Path, task_id: str, phase: str) -> None:
    """Update current_phase for a task（WS 推送在调用方）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(current_phase=phase, updated_at=_now())
        )


def update_progress(db_path: Path, task_id: str, progress_json: str) -> None:
    """Write structured progress JSON to DB（序列化在调用方）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(progress_json=progress_json, updated_at=_now())
        )


def recover_stale_tasks(db_path: Path) -> int:
    """Mark all running tasks as stopped (crash recovery)——返回清理数。

    batch1 D3 语义注记: 恢复类写入是「残留态清理」（WHERE status='running'
    的条件更新即其前态语义），不走 update_task 的动作前态守护。
    """
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        result = conn.execute(
            update(EvolutionTask)
            .where(EvolutionTask.status == "running")
            .values(status="stopped", stop_reason="crash_recovery",
                    updated_at=_now())
        )
        return result.rowcount


def restart_crashed_tasks(db_path: Path, max_restarts: int = 3) -> int:
    """Restart crashed tasks without champion（bounded retry，语义同旧轨）。"""
    engine = _engine_for(db_path)
    restarted = 0
    with engine.begin() as conn:
        rows = conn.execute(
            select(EvolutionTask.task_id, EvolutionTask.progress_json).where(
                EvolutionTask.status == "stopped",
                EvolutionTask.stop_reason.in_(["crash_recovery"]),
                (EvolutionTask.champion_dna.is_(None)) | (EvolutionTask.champion_dna == ""),
            )
        ).all()
        for task_id, progress_raw in rows:
            try:
                progress = json.loads(progress_raw) if progress_raw else {}
            except Exception:
                progress = {}
            if int(progress.get("restart_count", 0)) >= max_restarts:
                continue
            progress["restart_count"] = int(progress.get("restart_count", 0)) + 1
            conn.execute(
                update(EvolutionTask).where(EvolutionTask.task_id == task_id)
                .values(status="pending", progress_json=json.dumps(progress, ensure_ascii=False),
                        stop_reason=None, updated_at=_now())
            )
            restarted += 1
    return restarted


def find_pending_task(db_path: Path) -> Optional[Dict[str, Any]]:
    """取最早创建的 pending 任务（runner 拾取序）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(EvolutionTask)
            .where(EvolutionTask.status == "pending")
            .order_by(EvolutionTask.created_at.asc()).limit(1)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_top_snapshots(db_path: Path, task_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """按 best_score 倒序取代际快照（路由 top-strategies 用）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(GenerationSnapshot.generation, GenerationSnapshot.best_score,
                   GenerationSnapshot.best_dna)
            .where(GenerationSnapshot.task_id == task_id)
            .order_by(GenerationSnapshot.best_score.desc()).limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]


def update_current_generation(db_path: Path, task_id: str, generation: int) -> None:
    """代际游标推进（runner 每代）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(current_generation=generation, updated_at=_now())
        )


def update_champion_metrics(
    db_path: Path, task_id: str, *, best_fitness: float,
    champion_metrics: str, champion_dimension_scores: str,
) -> None:
    """冠军最终指标写入（tracker 一致快照）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(best_fitness=best_fitness, champion_metrics=champion_metrics,
                    champion_dimension_scores=champion_dimension_scores,
                    updated_at=_now())
        )


def update_oos_results(
    db_path: Path, task_id: str, *, oos_fitness: float,
    oos_qualified: int, oos_metrics: str,
) -> None:
    """样本外验证结果写入。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(oos_fitness=oos_fitness, oos_qualified=oos_qualified,
                    oos_metrics=oos_metrics, updated_at=_now())
        )


def update_champion_full(
    db_path: Path, task_id: str, *, best_score: float,
    champion_metrics: str, champion_dimension_scores: str,
) -> None:
    """冠军代际更新（best_score/best_fitness 同值，tracker 更新成功时）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(best_score=best_score, best_fitness=best_score,
                    champion_metrics=champion_metrics,
                    champion_dimension_scores=champion_dimension_scores,
                    updated_at=_now())
        )


def update_best_score_if_higher(db_path: Path, task_id: str, best_score: float) -> None:
    """仅当现有 best_score 更低或为 NULL 时更新（乐观锁语义）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(EvolutionTask)
            .where(EvolutionTask.task_id == task_id,
                   (EvolutionTask.best_score.is_(None)) | (EvolutionTask.best_score < best_score))
            .values(best_score=best_score, best_fitness=best_score,
                    updated_at=_now())
        )


def update_task_config(db_path: Path, task_id: str, **values: Any) -> None:
    """任务创建后的扩展配置写入（列由调用方白名单化）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        # 覆盖式合并（paper_trading/live_trading 先例）：updated_at 为
        # 仓储内部时间戳，调用方显式携带时以内部刷新为准
        conn.execute(
            update(EvolutionTask).where(EvolutionTask.task_id == task_id)
            .values(**{**values, "updated_at": _now()})
        )
