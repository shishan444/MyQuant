"""Extended database operations for v0.8 schema.

Adds 3 new tables (strategy, backtest_result, dataset_meta),
extends evolution_task with 6 columns, and provides a
version-managed migration runner on top of core.persistence.db.

Public API:
    init_db_ext           -- run migrations to create/upgrade schema
    save_strategy / get_strategy / list_strategies / update_strategy / delete_strategy
    save_backtest_result / list_backtest_results / get_backtest_result
    save_dataset_meta / list_datasets / get_dataset / update_dataset_stats / delete_dataset
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.persistence.db import init_db


def _connect(db_path: Path) -> sqlite3.Connection:
    """legacy init/ALTER 链专用连接（迁移工具属性；业务代码走 orm.engine 工厂）。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn

logger = logging.getLogger(__name__)

# Located at core/persistence/db_ext.py — resolve up to the repo root (which
# holds the migrations/ directory). parents[0]=persistence, [1]=core, [2]=root.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# ---------------------------------------------------------------------------

# -- helpers --

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- migration runner --

def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of already-applied migration version numbers."""
    try:
        rows = conn.execute(
            "SELECT version FROM schema_version"
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        # table does not exist yet -- first run
        return set()


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create the schema_version table if absent."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL
        )
    """)


_ALTER_COLUMNS = [
    ("champion_strategy_id", "TEXT"),
    ("population_size",      "INTEGER DEFAULT 15"),
    ("max_generations",      "INTEGER DEFAULT 200"),
    ("elite_ratio",          "REAL DEFAULT 0.5"),
    ("current_generation",   "INTEGER DEFAULT 0"),
]


def _apply_alter_evolution_task(conn: sqlite3.Connection) -> None:
    """Add extended columns to evolution_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None

    for col_name, col_def in _ALTER_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


_MTF_COLUMNS = [
    ("indicator_pool", "TEXT"),
    ("timeframe_pool", "TEXT"),
    ("mode", "TEXT"),
]

_STRATEGY_EXT_COLUMNS = [
    ("metrics_json", "TEXT"),  # JSON: {annual_return, sharpe_ratio, max_drawdown, win_rate, ...}
]

_CONSTRAINT_COLUMNS = [
    ("leverage", "INTEGER DEFAULT 1"),
    ("direction", "TEXT DEFAULT 'long'"),
    ("data_start", "TEXT"),
    ("data_end", "TEXT"),
    ("data_time_start", "TEXT"),
    ("data_time_end", "TEXT"),
    ("data_row_count", "INTEGER DEFAULT 0"),
    ("best_score", "REAL"),
    ("indicator_pool", "TEXT"),
    ("timeframe_pool", "TEXT"),
    ("mode", "TEXT"),
    ("champion_metrics", "TEXT"),
    ("champion_dimension_scores", "TEXT"),
    ("walk_forward_enabled", "INTEGER DEFAULT 0"),
    ("continuous", "INTEGER DEFAULT 1"),
]

_PROGRESS_COLUMNS = [
    ("current_phase", "TEXT DEFAULT 'idle'"),
    ("progress_json", "TEXT"),
    ("heartbeat_at", "TEXT"),
]

_SCORING_CONSTRAINT_COLUMNS = [
    ("min_annual_return", "REAL DEFAULT 0.10"),
    ("max_drawdown_limit", "REAL DEFAULT 0.10"),
]

# -- Fitness scoring columns (migration 021) --

_FITNESS_EVOLUTION_TASK_COLUMNS = [
    ("best_fitness",          "REAL"),
    ("champion_satisfaction",  "TEXT"),
    ("requirements_json",      "TEXT"),
    ("qualified_count",        "INTEGER DEFAULT 0"),
    ("target_fitness",         "REAL DEFAULT 1.0"),
]

_FITNESS_STRATEGY_COLUMNS = [
    ("best_fitness", "REAL"),
    ("qualified",    "INTEGER DEFAULT NULL"),
]

_FITNESS_BACKTEST_RESULT_COLUMNS = [
    ("fitness",          "REAL DEFAULT 0.0"),
    ("qualified",        "INTEGER DEFAULT 0"),
    ("satisfaction_json", "TEXT"),
]

_OOS_VALIDATION_COLUMNS = [
    ("oos_fitness",   "REAL"),
    ("oos_qualified", "INTEGER DEFAULT 0"),
    ("oos_metrics",   "TEXT"),
]

_VERIFY_STRATEGY_COLUMNS = [
    ("verify_count",      "INTEGER DEFAULT 0"),
    ("verify_avg_score",  "REAL"),
    ("verify_best_score", "REAL"),
    ("last_verified_at",  "TEXT"),
    ("verify_star",       "INTEGER DEFAULT NULL"),
]


def _apply_constraint_columns(conn: sqlite3.Connection) -> None:
    """Add leverage/direction and data range columns to evolution_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None

    for col_name, col_def in _CONSTRAINT_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


def _apply_mtf_columns(conn: sqlite3.Connection) -> None:
    """Add MTF-related columns to evolution_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None

    for col_name, col_def in _MTF_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


def _apply_progress_columns(conn: sqlite3.Connection) -> None:
    """Add progress tracking columns to evolution_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None

    for col_name, col_def in _PROGRESS_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


def _apply_scoring_constraint_columns(conn: sqlite3.Connection) -> None:
    """Add min_annual_return and max_drawdown_limit to evolution_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None

    for col_name, col_def in _SCORING_CONSTRAINT_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


def _apply_strategy_ext_columns(conn: sqlite3.Connection) -> None:
    """Add metrics_json column to strategy table (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(strategy)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None

    for col_name, col_def in _STRATEGY_EXT_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE strategy ADD COLUMN {col_name} {col_def}"
            )


def _apply_fitness_columns(conn: sqlite3.Connection) -> None:
    """Add fitness scoring columns to evolution_task, strategy, and backtest_result (idempotent).

    Migration 021: new fitness-based scoring replaces score_strategy (0-100 weighted sum)
    with compute_fitness (satisfaction ratio product + qualified boolean gate).
    Old columns are kept for backward compatibility.
    """
    tables_columns = [
        ("evolution_task", _FITNESS_EVOLUTION_TASK_COLUMNS),
        ("strategy", _FITNESS_STRATEGY_COLUMNS),
        ("backtest_result", _FITNESS_BACKTEST_RESULT_COLUMNS),
    ]
    for table_name, columns in tables_columns:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        existing = {row[1] for row in cursor.fetchall()}
        conn.row_factory = None
        for col_name, col_def in columns:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"
                )


def _apply_oos_validation_columns(conn: sqlite3.Connection) -> None:
    """Add OOS validation columns to evolution_task (idempotent).

    Migration 022: out-of-sample validation columns for champion strategies.
    """
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    for col_name, col_def in _OOS_VALIDATION_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


# -- Verify session columns (migration 023) --

_VERIFY_SESSION_BT_COLUMNS = [
    ("session_id", "TEXT"),
]


def _create_verify_session_table(conn: sqlite3.Connection) -> None:
    """Create verify_session table (idempotent)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS verify_session (
        session_id       TEXT PRIMARY KEY,
        status           TEXT NOT NULL DEFAULT 'running',
        strategy_ids     TEXT NOT NULL,
        data_ranges      TEXT NOT NULL,
        init_cash        REAL NOT NULL DEFAULT 100000,
        fee              REAL NOT NULL DEFAULT 0.001,
        slippage         REAL NOT NULL DEFAULT 0.0005,
        summary_json     TEXT,
        total_results    INTEGER DEFAULT 0,
        total_strategies INTEGER DEFAULT 0,
        error_message    TEXT,
        created_at       TEXT NOT NULL,
        completed_at     TEXT
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_verify_session_created "
        "ON verify_session(created_at DESC)"
    )


def _apply_verify_session_columns(conn: sqlite3.Connection) -> None:
    """Add session_id column to backtest_result (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(backtest_result)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    for col_name, col_def in _VERIFY_SESSION_BT_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE backtest_result ADD COLUMN {col_name} {col_def}"
            )


def _apply_verify_strategy_columns(conn: sqlite3.Connection) -> None:
    """Add verification summary columns to strategy (idempotent).

    Migration 024: track how many times a strategy has been verified,
    its average and best comprehensive scores, and last verification time.
    """
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(strategy)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    for col_name, col_def in _VERIFY_STRATEGY_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE strategy ADD COLUMN {col_name} {col_def}"
            )


def _record_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, _now()),
    )


# -- public init --

def init_db_ext(db_path: Path) -> None:
    """Initialize the extended schema.

    Calls core ``init_db`` first, then runs any pending SQL migrations
    from the *migrations/* directory, plus the ALTER TABLE for extended
    evolution_task columns.

    Safe to call multiple times (idempotent).
    """
    # 1. Ensure core tables exist
    init_db(db_path)

    conn = _connect(db_path)
    try:
        _ensure_schema_version_table(conn)
        applied = _get_applied_versions(conn)

        # 2. SQL 轨退役（B3d.1）：建表语义已由 init_db 的 ORM 元数据承接
        #（CREATE IF NOT EXISTS 被抢先满足；008 的裸 ALTER 会对 ORM 建好的
        # 列撞 duplicate column）。migrations/*.sql 保留为历史文档，schema
        # 演进权归 Alembic 版本链、建表权归 ORM 模型。历史库的列补齐由
        # 下方 Python ALTER 轨（PRAGMA guard 幂等）继续承载。

        # 3. Always ensure schema_version table is recorded
        if 1 not in applied:
            _record_version(conn, 1)

        # 4. Data-driven ALTER/data migrations (versions 5-25).
        # Defined inline (after all _apply_*/_create_* helpers are in scope)
        # so we iterate a table instead of 24 hard-coded sequential steps.
        # Runners are idempotent (PRAGMA table_info guards).
        def _step_23(c: sqlite3.Connection) -> None:
            _create_verify_session_table(c)
            _apply_verify_session_columns(c)

        migrations = [
            (5, _apply_alter_evolution_task),
            (6, _apply_mtf_columns),
            (7, _apply_constraint_columns),
            (8, _apply_strategy_ext_columns),
            (9, _apply_progress_columns),
            (10, _create_paper_trading_tables),
            (11, _create_equity_snapshot_table),
            (12, _apply_scoring_constraint_columns),
            (13, _apply_execution_model_column),
            (14, _apply_bars_held_column),
            (15, _apply_position_open_cost_column),
            (16, _apply_pending_decision_column),
            (17, _apply_confidence_sizing_column),
            (18, _apply_prediction_dna_column),
            (19, _dedup_strategies),
            (20, _recompute_strategy_names),
            (21, _apply_fitness_columns),
            (22, _apply_oos_validation_columns),
            (23, _step_23),
            (24, _apply_verify_strategy_columns),
            (25, _apply_verify_strategy_columns),
            # B3c.1: live 表收编进 init 链（原 core/live_trading/persistence.py）
            (26, _create_live_trading_tables),
        ]
        for version, runner in migrations:
            runner(conn)
            if version not in applied:
                _record_version(conn, version)

        conn.commit()
    finally:
        conn.close()


# ===================================================================
# Strategy dedup cleanup (migration 019)
# ===================================================================

def _dedup_strategies(conn: sqlite3.Connection) -> None:
    """Remove duplicate strategies, keeping the one with the highest best_score per gene_signature."""
    try:
        # Find duplicates: gene_signature appears more than once
        dup_sigs = conn.execute(
            """SELECT gene_signature FROM strategy
               WHERE gene_signature IS NOT NULL
               GROUP BY gene_signature
               HAVING COUNT(*) > 1"""
        ).fetchall()
        if not dup_sigs:
            return

        for (sig,) in dup_sigs:
            # Get all rows with this signature, ordered by best_score DESC (NULLs last)
            rows = conn.execute(
                """SELECT strategy_id, best_score FROM strategy
                   WHERE gene_signature = ?
                   ORDER BY CASE WHEN best_score IS NULL THEN 1 ELSE 0 END, best_score DESC""",
                (sig,),
            ).fetchall()
            # Keep the first (highest score), delete the rest
            keep_id = rows[0][0]
            delete_ids = [r[0] for r in rows[1:]]
            if delete_ids:
                placeholders = ",".join("?" * len(delete_ids))
                conn.execute(
                    f"DELETE FROM strategy WHERE strategy_id IN ({placeholders})",
                    delete_ids,
                )
        logger.info("Deduped %d strategy signatures", len(dup_sigs))
    except Exception:
        logger.warning("Strategy dedup failed", exc_info=True)


def _recompute_strategy_names(conn: sqlite3.Connection) -> None:
    """Recompute all strategy names to include MTF layer data in the hash."""
    try:
        from core.strategy.dna import StrategyDNA, generate_strategy_name

        rows = conn.execute(
            "SELECT strategy_id, dna_json FROM strategy WHERE dna_json IS NOT NULL"
        ).fetchall()
        updated = 0
        for strategy_id, dna_json in rows:
            try:
                dna = StrategyDNA.from_json(dna_json)
                new_name = generate_strategy_name(dna)
                conn.execute(
                    "UPDATE strategy SET name = ? WHERE strategy_id = ?",
                    (new_name, strategy_id),
                )
                updated += 1
            except Exception:
                continue
        if updated:
            logger.info("Recomputed %d strategy names", updated)
    except Exception:
        logger.warning("Strategy name recomputation failed", exc_info=True)
# ===================================================================

def _create_paper_trading_tables(conn: sqlite3.Connection) -> None:
    """Create paper_trading_task and paper_trade tables (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trading_task (
            task_id          TEXT PRIMARY KEY,
            status           TEXT NOT NULL DEFAULT 'pending',
            strategy_name    TEXT,
            symbol           TEXT NOT NULL DEFAULT 'BTCUSDT',
            timeframe        TEXT NOT NULL DEFAULT '4h',
            initial_cash     REAL NOT NULL DEFAULT 100000,
            fee              REAL NOT NULL DEFAULT 0.001,
            leverage         INTEGER NOT NULL DEFAULT 1,
            direction        TEXT NOT NULL DEFAULT 'long',
            dna_json         TEXT NOT NULL,
            score_template   TEXT DEFAULT 'explorer',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            started_at       TEXT,
            stopped_at       TEXT,
            stop_reason      TEXT,
            -- Position state (for recovery)
            position_side     TEXT,
            position_entry    REAL,
            position_quantity REAL,
            position_margin   REAL,
            position_funding  REAL DEFAULT 0,
            -- Account state
            balance          REAL,
            unrealized_pnl   REAL DEFAULT 0,
            -- Stats
            total_trades     INTEGER DEFAULT 0,
            total_pnl        REAL DEFAULT 0,
            win_count        INTEGER DEFAULT 0,
            loss_count       INTEGER DEFAULT 0,
            -- Last processed bar
            last_bar_time    TEXT,
            last_bar_close   REAL,
            -- Heartbeat
            heartbeat_at     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trade (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT NOT NULL,
            bar_time    TEXT NOT NULL,
            side        TEXT NOT NULL,
            action      TEXT NOT NULL,
            price       REAL NOT NULL,
            quantity    REAL NOT NULL,
            pnl         REAL,
            fee_paid    REAL DEFAULT 0,
            reason      TEXT,
            FOREIGN KEY (task_id) REFERENCES paper_trading_task(task_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_paper_trade_task
        ON paper_trade(task_id, bar_time)
    """)
    # B1.3 幂等迁移：bar 游标与事件落库的原子性兜底——同 (task_id, bar_time,
    # action) 唯一。存量重复（历史重放 bug 产物）保留最早一条后建唯一索引
    conn.execute("""
        DELETE FROM paper_trade
        WHERE id NOT IN (
            SELECT MIN(id) FROM paper_trade
            GROUP BY task_id, bar_time, action
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trade_unique
        ON paper_trade(task_id, bar_time, action)
    """)


def _apply_execution_model_column(conn: sqlite3.Connection) -> None:
    """Add execution_model column to paper_trading_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "execution_model" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN execution_model TEXT DEFAULT 'v1'"
        )


def _apply_bars_held_column(conn: sqlite3.Connection) -> None:
    """Add bars_held column to paper_trading_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "bars_held" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN bars_held INTEGER DEFAULT 0"
        )


def _apply_position_open_cost_column(conn: sqlite3.Connection) -> None:
    """Add position_open_cost column to paper_trading_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "position_open_cost" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN position_open_cost REAL DEFAULT 0"
        )


def _apply_pending_decision_column(conn: sqlite3.Connection) -> None:
    """Add pending_decision_json column to paper_trading_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "pending_decision_json" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN pending_decision_json TEXT"
        )


def _apply_confidence_sizing_column(conn: sqlite3.Connection) -> None:
    """Add confidence_sizing_enabled column to paper_trading_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "confidence_sizing_enabled" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN confidence_sizing_enabled INTEGER DEFAULT 0"
        )


def _apply_prediction_dna_column(conn: sqlite3.Connection) -> None:
    """Add prediction_dna_json column to paper_trading_task (idempotent)."""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "prediction_dna_json" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN prediction_dna_json TEXT"
        )


def _create_equity_snapshot_table(conn: sqlite3.Connection) -> None:
    """Create paper_equity_snapshot table (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_equity_snapshot (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            equity          REAL NOT NULL,
            balance         REAL NOT NULL,
            unrealized_pnl  REAL DEFAULT 0,
            position_side   TEXT DEFAULT 'flat'
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_equity_task_time
        ON paper_equity_snapshot(task_id, timestamp)
    """)


# -- Paper Trading Task CRUD --

# ===================================================================
# Live Trading Task（B3c.1 收编：原 core/live_trading/persistence.py）
# ===================================================================

def _create_live_trading_tables(conn: sqlite3.Connection) -> None:
    """Create live_trading_task table (idempotent，收编自 live 轨)。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_trading_task (
            task_id       TEXT PRIMARY KEY,
            symbol        TEXT NOT NULL,
            timeframe     TEXT NOT NULL,
            dna_json      TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            stop_reason   TEXT,
            direction     TEXT DEFAULT 'mixed',
            position_side TEXT DEFAULT '',
            position_qty  REAL DEFAULT 0,
            position_entry REAL DEFAULT 0,
            position_sl   REAL,
            position_tp   REAL,
            balance       REAL DEFAULT 0,
            equity        REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            total_trades  INTEGER DEFAULT 0,
            total_pnl     REAL DEFAULT 0,
            win_count     INTEGER DEFAULT 0,
            loss_count    INTEGER DEFAULT 0,
            last_bar_time TEXT,
            heartbeat_at  TEXT,
            config_json   TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
