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

from core.persistence.db import _connect, init_db
from core.scoring import trading_metrics

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

        # 2. Run numbered SQL migrations
        if _MIGRATIONS_DIR.is_dir():
            sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
            for sql_file in sql_files:
                # extract leading digits as version number
                version_str = ""
                for ch in sql_file.name:
                    if ch.isdigit():
                        version_str += ch
                    else:
                        break
                if not version_str:
                    continue
                version = int(version_str)
                if version in applied:
                    continue
                sql_text = sql_file.read_text(encoding="utf-8")
                # Skip migration 005/006 — handled separately via ALTER
                if version in (5, 6):
                    continue
                conn.executescript(sql_text)
                _record_version(conn, version)
                logger.info("Applied migration %s", sql_file.name)

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


def save_paper_trading_task(
    db_path: Path,
    *,
    task_id: str,
    dna_json: str,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    initial_cash: float = 100_000,
    fee: float = 0.001,
    leverage: int = 1,
    direction: str = "long",
    score_template: str = "explorer",
    strategy_name: Optional[str] = None,
    confidence_sizing_enabled: bool = False,
    prediction_dna_json: Optional[str] = None,
) -> None:
    """Insert a paper trading task（B3c.1 起委托 repository，签名不变）。"""
    from core.persistence.repository import paper_trading as repo
    repo.save_paper_trading_task(
        db_path, task_id=task_id, dna_json=dna_json, symbol=symbol,
        timeframe=timeframe, initial_cash=initial_cash, fee=fee,
        leverage=leverage, direction=direction, score_template=score_template,
        strategy_name=strategy_name,
        confidence_sizing_enabled=confidence_sizing_enabled,
        prediction_dna_json=prediction_dna_json,
    )


def get_paper_trading_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a paper trading task（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.get_paper_trading_task(db_path, task_id)


def update_paper_trading_task(db_path: Path, task_id: str, **kwargs) -> None:
    """Update task fields（委托 repository，白名单不变）。"""
    from core.persistence.repository import paper_trading as repo
    repo.update_paper_trading_task(db_path, task_id, **kwargs)


def list_paper_trading_tasks(
    db_path: Path, status: Optional[str] = None, limit: int = 50, offset: int = 0,
) -> List[Dict[str, Any]]:
    """List paper trading tasks（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.list_paper_trading_tasks(db_path, status=status, limit=limit, offset=offset)


def count_paper_trading_tasks(
    db_path: Path, status: Optional[str] = None,
) -> int:
    """Count paper trading tasks（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.count_paper_trading_tasks(db_path, status=status)


def save_paper_trade(
    db_path: Path,
    *,
    task_id: str,
    bar_time: str,
    side: str,
    action: str,
    price: float,
    quantity: float,
    pnl: Optional[float] = None,
    fee_paid: float = 0.0,
    reason: Optional[str] = None,
) -> None:
    """Insert a paper trade（委托 repository；INSERT OR IGNORE 幂等，B1.3）。"""
    from core.persistence.repository import paper_trading as repo
    repo.save_paper_trade(
        db_path, task_id=task_id, bar_time=bar_time, side=side, action=action,
        price=price, quantity=quantity, pnl=pnl, fee_paid=fee_paid, reason=reason,
    )


def list_paper_trades(
    db_path: Path, task_id: str, limit: int = 100,
) -> List[Dict[str, Any]]:
    """List paper trades（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.list_paper_trades(db_path, task_id, limit=limit)


def count_paper_trades(db_path: Path, task_id: str) -> int:
    """Count paper trades（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.count_paper_trades(db_path, task_id)


def save_equity_snapshots(
    db_path: Path, task_id: str, snapshots: list[dict],
) -> None:
    """Batch insert equity snapshots（委托 repository，OR IGNORE 幂等）。"""
    from core.persistence.repository import paper_trading as repo
    repo.save_equity_snapshots(db_path, task_id, snapshots)


def list_equity_snapshots(
    db_path: Path, task_id: str,
) -> List[Dict[str, Any]]:
    """List equity snapshots（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.list_equity_snapshots(db_path, task_id)


def delete_paper_trading_task(db_path: Path, task_id: str) -> bool:
    """Delete a paper trading task and all associated data（委托 repository）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.delete_paper_trading_task(db_path, task_id)


def compute_trading_metrics(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """Compute performance metrics（委托 repository；公式单源 core.scoring）。"""
    from core.persistence.repository import paper_trading as repo
    return repo.compute_trading_metrics(db_path, task_id)


# Strategy CRUD
# ===================================================================

def save_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    dna_json: str,
    symbol: str,
    timeframe: str,
    name: Optional[str] = None,
    source: str = "manual",
    source_task_id: Optional[str] = None,
    best_score: Optional[float] = None,
    generation: int = 0,
    parent_ids: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    metrics_json: Optional[str] = None,
    gene_signature: Optional[str] = None,
) -> None:
    """Insert a new strategy record（B3b.1 起委托 repository，签名不变）。"""
    from core.persistence.repository import strategy as repo
    repo.save_strategy(
        db_path, strategy_id=strategy_id, dna_json=dna_json, symbol=symbol,
        timeframe=timeframe, name=name, source=source,
        source_task_id=source_task_id, best_score=best_score,
        generation=generation, parent_ids=parent_ids, tags=tags, notes=notes,
        metrics_json=metrics_json, gene_signature=gene_signature,
    )


def get_strategy(db_path: Path, strategy_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single strategy by ID（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    return repo.get_strategy(db_path, strategy_id)


def count_strategies_by_tasks(
    db_path: Path,
    task_ids: List[str],
    min_score: float = 0,
) -> Dict[str, int]:
    """Batch count strategies per task_id（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    return repo.count_strategies_by_tasks(db_path, task_ids, min_score)


def count_strategies(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    source_task_id: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
) -> int:
    """Count strategies with optional filtering（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    return repo.count_strategies(
        db_path, symbol=symbol, source=source, source_task_id=source_task_id,
        tags=tags, qualified=qualified,
    )


def list_strategies(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    source_task_id: Optional[str] = None,
    tags: Optional[str] = None,
    qualified: Optional[bool] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List strategies with filtering, sorting, pagination（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    return repo.list_strategies(
        db_path, symbol=symbol, source=source, source_task_id=source_task_id,
        tags=tags, qualified=qualified, sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
    )


def update_strategy(
    db_path: Path,
    *,
    strategy_id: str,
    **fields: Any,
) -> None:
    """Update one or more fields on an existing strategy（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    repo.update_strategy(db_path, strategy_id=strategy_id, **fields)


def delete_strategy(db_path: Path, strategy_id: str) -> None:
    """Delete a strategy by ID（委托 repository）。"""
    from core.persistence.repository import strategy as repo
    repo.delete_strategy(db_path, strategy_id)


# Backtest Result
# ===================================================================

def save_backtest_result(
    db_path: Path,
    *,
    result_id: str,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    data_start: str,
    data_end: str,
    init_cash: float = 100000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    total_return: float = 0.0,
    sharpe_ratio: float = 0.0,
    max_drawdown: float = 0.0,
    win_rate: float = 0.0,
    total_trades: int = 0,
    total_score: float = 0.0,
    template_name: str = "explorer",
    dimension_scores: Optional[str] = None,
    equity_curve: Optional[str] = None,
    trades_json: Optional[str] = None,
    run_source: str = "lab",
    fitness: float = 0.0,
    qualified: int = 0,
    satisfaction_json: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Insert a backtest result record（B3b.1 起委托 repository，签名不变）。"""
    from core.persistence.repository import backtest_result as repo
    repo.save_backtest_result(
        db_path, result_id=result_id, strategy_id=strategy_id, symbol=symbol,
        timeframe=timeframe, data_start=data_start, data_end=data_end,
        init_cash=init_cash, fee=fee, slippage=slippage,
        total_return=total_return, sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown, win_rate=win_rate, total_trades=total_trades,
        total_score=total_score, template_name=template_name,
        dimension_scores=dimension_scores, equity_curve=equity_curve,
        trades_json=trades_json, run_source=run_source, fitness=fitness,
        qualified=qualified, satisfaction_json=satisfaction_json,
        session_id=session_id,
    )


def get_backtest_result(db_path: Path, result_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single backtest result by ID（委托 repository）。"""
    from core.persistence.repository import backtest_result as repo
    return repo.get_backtest_result(db_path, result_id)


def list_backtest_results(
    db_path: Path,
    *,
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    run_source: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List backtest results with optional filtering（委托 repository）。"""
    from core.persistence.repository import backtest_result as repo
    return repo.list_backtest_results(
        db_path, strategy_id=strategy_id, symbol=symbol, run_source=run_source,
        sort_by=sort_by, sort_order=sort_order, limit=limit,
    )


# Verify Session CRUD
# ===================================================================

def save_verify_session(
    db_path: Path,
    *,
    session_id: str,
    strategy_ids: str,
    data_ranges: str,
    init_cash: float = 100000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    status: str = "running",
) -> None:
    """Insert a verify session record（B3b.1 起委托 repository，签名不变）。"""
    from core.persistence.repository import verify_session as repo
    repo.save_verify_session(
        db_path, session_id=session_id, strategy_ids=strategy_ids,
        data_ranges=data_ranges, init_cash=init_cash, fee=fee,
        slippage=slippage, status=status,
    )


def get_verify_session(db_path: Path, session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a verify session by ID（委托 repository）。"""
    from core.persistence.repository import verify_session as repo
    return repo.get_verify_session(db_path, session_id)


def update_verify_session(
    db_path: Path,
    session_id: str,
    *,
    status: Optional[str] = None,
    summary_json: Optional[str] = None,
    total_results: Optional[int] = None,
    total_strategies: Optional[int] = None,
    error_message: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    """Update verify session fields（委托 repository，非 None 字段语义不变）。"""
    from core.persistence.repository import verify_session as repo
    repo.update_verify_session(
        db_path, session_id, status=status, summary_json=summary_json,
        total_results=total_results, total_strategies=total_strategies,
        error_message=error_message, completed_at=completed_at,
    )


def list_verify_sessions(
    db_path: Path,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List verify sessions（委托 repository）。"""
    from core.persistence.repository import verify_session as repo
    return repo.list_verify_sessions(db_path, limit)


# Dataset Metadata CRUD
# ===================================================================

def save_dataset_meta(
    db_path: Path,
    *,
    dataset_id: str,
    symbol: str,
    interval: str,
    parquet_path: str,
    row_count: int = 0,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    file_size_bytes: int = 0,
    source: str = "csv_import",
    format_detected: Optional[str] = None,
    timestamp_precision: Optional[str] = None,
    ohlcv_stats: Optional[str] = None,
    gap_count: int = 0,
    quality_status: str = "unknown",
    quality_notes: Optional[str] = None,
    import_batch_id: Optional[str] = None,
) -> None:
    """Insert a dataset metadata record（B3a.3 起委托 repository，签名不变）。"""
    from core.persistence.repository import dataset_meta as repo
    repo.save_dataset_meta(
        db_path, dataset_id=dataset_id, symbol=symbol, interval=interval,
        parquet_path=parquet_path, row_count=row_count, time_start=time_start,
        time_end=time_end, file_size_bytes=file_size_bytes, source=source,
        format_detected=format_detected,
        timestamp_precision=timestamp_precision, ohlcv_stats=ohlcv_stats,
        gap_count=gap_count, quality_status=quality_status,
        quality_notes=quality_notes, import_batch_id=import_batch_id,
    )


def get_dataset(db_path: Path, dataset_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single dataset by ID, or None if not found（委托 repository）。"""
    from core.persistence.repository import dataset_meta as repo
    return repo.get_dataset(db_path, dataset_id)


def list_datasets(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List datasets with optional filtering（委托 repository）。"""
    from core.persistence.repository import dataset_meta as repo
    return repo.list_datasets(
        db_path, symbol=symbol, interval=interval, limit=limit,
    )


def update_dataset_stats(
    db_path: Path,
    *,
    dataset_id: str,
    **fields: Any,
) -> None:
    """Update fields on a dataset record（委托 repository，白名单不变）。"""
    from core.persistence.repository import dataset_meta as repo
    repo.update_dataset_stats(db_path, dataset_id=dataset_id, **fields)


def delete_dataset(db_path: Path, dataset_id: str) -> None:
    """Delete a dataset by ID（委托 repository）。"""
    from core.persistence.repository import dataset_meta as repo
    repo.delete_dataset(db_path, dataset_id)
