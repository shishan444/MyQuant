"""实盘任务持久化（第9步）—— ``live_trading_task`` 表，独立于 ``paper_trading_task``。

仿 ``db_ext`` 模式（save 创建 / update 改状态 / get / list / delete）。
**持仓/订单状态以交易所为准**（sync_state 第7步 Runner 拉），DB 是缓存 + 审计。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LIVE_TASK_TABLE = "live_trading_task"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_live_trading_tables(db_path: Path) -> None:
    """建 live_trading_task 表（幂等）。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {LIVE_TASK_TABLE} (
                task_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                dna_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stop_reason TEXT,
                direction TEXT DEFAULT 'mixed',
                position_side TEXT DEFAULT '',
                position_qty REAL DEFAULT 0,
                position_entry REAL DEFAULT 0,
                position_sl REAL,
                position_tp REAL,
                balance REAL DEFAULT 0,
                equity REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                last_bar_time TEXT,
                heartbeat_at TEXT,
                config_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_live_trading_task(
    db_path: Path,
    task_id: str,
    symbol: str,
    timeframe: str,
    dna_json: str,
    direction: str = "mixed",
    config_json: Optional[str] = None,
    status: str = "pending",
) -> None:
    """创建/重置任务记录（INSERT OR REPLACE，首次创建用此，状态变更用 update）。"""
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {LIVE_TASK_TABLE}
            (task_id, symbol, timeframe, dna_json, direction, status, config_json,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, symbol, timeframe, dna_json, direction, status, config_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_live_trading_task(db_path: Path, task_id: str) -> Optional[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT * FROM {LIVE_TASK_TABLE} WHERE task_id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_live_trading_task(db_path: Path, task_id: str, **kwargs: Any) -> None:
    """更新任务字段（自动刷新 updated_at）。"""
    if not kwargs:
        return
    kwargs["updated_at"] = _now_iso()
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [task_id]
    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE {LIVE_TASK_TABLE} SET {cols} WHERE task_id=?", vals)
        conn.commit()
    finally:
        conn.close()


def list_live_trading_tasks(
    db_path: Path, status: Optional[str] = None
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if status:
            rows = conn.execute(
                f"SELECT * FROM {LIVE_TASK_TABLE} WHERE status=? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {LIVE_TASK_TABLE} ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_live_trading_task(db_path: Path, task_id: str) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"DELETE FROM {LIVE_TASK_TABLE} WHERE task_id=?", (task_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_heartbeat(db_path: Path, task_id: str) -> None:
    """刷新心跳时间戳。"""
    update_live_trading_task(db_path, task_id, heartbeat_at=_now_iso())
