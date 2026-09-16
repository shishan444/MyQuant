"""SQLite database layer for evolution persistence.

3 tables:
- evolution_task: one row per evolution run
- generation_snapshot: full population per generation (for checkpoint resume)
- evolution_history: lightweight score records (for UI charts)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.strategy.dna import StrategyDNA


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def connect(db_path: Path) -> sqlite3.Connection:
    """Public connection helper for external modules.

    core.persistence owns connection setup (WAL / synchronous / busy_timeout /
    row_factory). External modules (e.g. core.trading.runner) should use this
    instead of reaching into the private _connect, so persistence stays the
    single DB access point. Internally db.py still uses _connect directly.
    """
    return _connect(db_path)


def init_db(db_path: Path) -> None:
    """Create tables if not exist（B3d.1 起经 ORM 元数据建表，模型为唯一事实源）。"""
    from core.persistence.orm import Base, create_db_engine
    engine = create_db_engine(db_path)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def save_task(db_path: Path, task_id: str, target_score: float, template: str,
             symbol: str, timeframe: str, initial_dna, status: str = "pending") -> None:
    """Create a new evolution task（B3d.1 起委托 repository，签名不变）。"""
    from core.persistence.repository import evolution as repo
    repo.save_task(db_path, task_id, target_score, template, symbol, timeframe,
                   initial_dna, status=status)


def update_task(db_path: Path, task_id: str, status: Optional[str] = None,
                champion_dna=None, stop_reason: Optional[str] = None,
                expected_statuses: Optional[tuple] = None) -> bool:
    """Update task status/champion/stop_reason（委托 repository，D3 条件更新语义不变）。"""
    from core.persistence.repository import evolution as repo
    return repo.update_task(db_path, task_id, status=status, champion_dna=champion_dna,
                            stop_reason=stop_reason,
                            expected_statuses=expected_statuses)


def get_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """Get a task by ID（委托 repository）。"""
    from core.persistence.repository import evolution as repo
    return repo.get_task(db_path, task_id)


def save_snapshot(db_path: Path, task_id: str, generation: int, best_score: float,
                  avg_score: float, best_dna, population, mutation_log: Optional[str] = None,
                  wf_months: Optional[str] = None) -> None:
    """Save a generation snapshot（委托 repository）。"""
    from core.persistence.repository import evolution as repo
    repo.save_snapshot(db_path, task_id, generation, best_score, avg_score, best_dna,
                       population, mutation_log=mutation_log, wf_months=wf_months)


def save_history(db_path: Path, task_id: str, generation: int, best_score: float,
                 avg_score: float, top3_summary: str) -> None:
    """Save a lightweight history record（委托 repository）。"""
    from core.persistence.repository import evolution as repo
    repo.save_history(db_path, task_id, generation, best_score, avg_score, top3_summary)


def get_history(db_path: Path, task_id: str) -> List[Dict[str, Any]]:
    """Get all history records for a task（委托 repository）。"""
    from core.persistence.repository import evolution as repo
    return repo.get_history(db_path, task_id)


def list_all_tasks(db_path: Path, status: Optional[str] = None, limit: int = 50,
                   offset: int = 0) -> List[Dict[str, Any]]:
    """List tasks（委托 repository）。"""
    from core.persistence.repository import evolution as repo
    return repo.list_all_tasks(db_path, status=status, limit=limit, offset=offset)


def count_all_tasks(db_path: Path, status: Optional[str] = None) -> int:
    """Count total tasks（委托 repository）。"""
    from core.persistence.repository import evolution as repo
    return repo.count_all_tasks(db_path, status=status)
