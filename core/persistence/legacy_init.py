"""存量库幂等升级轨（legacy init/ALTER 链，B3d.1 收编自 db_ext）。

职能：把历史版本库零重建升级到当前 schema——建表权在 ORM 模型
（init_db 以元数据 CREATE IF NOT EXISTS 抢先满足）、schema 演进权在
Alembic 版本链，本模块只承载历史库的列补齐（PRAGMA guard 幂等
ALTER）与两笔数据迁移（版本 19 去重 / 20 策略名重算）。
价值：存量库无需重建即达终态列结构（ORM models 即以其终态库
PRAGMA 实测为列级权威源）。
服务对象：api/app.py 启动 lifespan（经 db_ext re-export 调
init_db_ext，每次启动幂等跑一遍）；_connect 另经 db_ext re-export
供测试库直连消费。
职责边界：只动 schema_version 记账与本轨迁移；表访问 CRUD 一律在
core.persistence.repository.*（db_ext 同名委托），本模块不持业务
读写。
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

# ---------------------------------------------------------------------------

def _now() -> str:
    """迁移链专用 UTC ISO 时间戳。

    与公共源 core.persistence.timestamps.utc_now_iso 并存——该模块
    docstring 裁明「db_ext legacy 链的 _now 随迁移工具链保留」，本轨
    自持一份不改引公共名。
    """
    return datetime.now(timezone.utc).isoformat()


# -- 迁移版本记账 --

def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    """读 schema_version 已应用版本号集合。

    缺失语义：表尚不存在（首次运行）返回空集而非报错——首跑即
    全量执行各 runner（幂等无害）。
    """
    try:
        rows = conn.execute(
            "SELECT version FROM schema_version"
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        # 表不存在——首跑路径
        return set()


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """确保版本记账表存在（CREATE IF NOT EXISTS，幂等）。"""
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
    """evolution_task 补早期扩展列（_ALTER_COLUMNS 5 列，幂等）。

    列补齐族通用契约：PRAGMA table_info 求现有列集合→缺失列逐个
    ALTER ADD（带 DEFAULT 的立即生效于存量行）；列已存在 no-op；
    ALTER 失败直接上抛（不吞）。
    """
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
    # 指标摘要 JSON：{annual_return, sharpe_ratio, max_drawdown, win_rate, ...}
    ("metrics_json", "TEXT"),
    # 生命周期状态（improve-mcp-agent-loop M4；PRAGMA guard 幂等补列）
    ("status", "TEXT NOT NULL DEFAULT 'active'"),  # draft | active | archived
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
    """evolution_task 补杠杆/方向约束与数据探测列（_CONSTRAINT_COLUMNS，幂等；契约同 _apply_alter_evolution_task）。"""
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
    """evolution_task 补多周期池列（_MTF_COLUMNS：indicator_pool/timeframe_pool/mode，幂等；契约同族）。"""
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
    """evolution_task 补进度/心跳列（_PROGRESS_COLUMNS，幂等；契约同族）。"""
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
    """evolution_task 补评分约束列（min_annual_return/max_drawdown_limit，幂等；契约同族）。"""
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
    """strategy 表补指标摘要与生命周期状态列（幂等；契约同族，目标表为 strategy）。"""
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
    """三表补 fitness 评分列（migration 021，幂等）。

    语义：fitness 体系（满意度乘积 + qualified 布尔门）替代旧
    score_strategy（0-100 加权和）；旧列保留兼容不删。契约同列补齐
    族，差异点为按表分组循环（evolution_task/strategy/backtest_result）。
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
    """evolution_task 补样本外验证列（migration 022：冠军策略 OOS 结果，幂等；契约同族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(evolution_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    for col_name, col_def in _OOS_VALIDATION_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE evolution_task ADD COLUMN {col_name} {col_def}"
            )


# -- 验证会话（migration 023） --

_VERIFY_SESSION_BT_COLUMNS = [
    ("session_id", "TEXT"),
]


def _create_verify_session_table(conn: sqlite3.Connection) -> None:
    """建 verify_session 表（CREATE IF NOT EXISTS + created_at 索引，幂等）。

    与 ORM models.VerifySession 镜像：对历史库生效，新库被 ORM 元数据
    建表抢先满足（no-op）。
    """
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
    """backtest_result 补 session_id 关联列（幂等；契约同列补齐族）。"""
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
    """strategy 补验证摘要列（migration 024：验证次数/均分/最高分与
    最近验证时刻，幂等；契约同族）。

    版本 24/25 双挂本函数（init_db_ext migrations 列表）：引入时即
    同函数进入（9c34908），版本 25 从无独立迁移内容；幂等无害
    （PRAGMA guard 二次执行 no-op）。版本号已随历史库持久化进
    schema_version 不可回收——删 25 行会使已记账库重跑（仍无害但
    记账漂移），故保留双挂。
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
    """版本号记账（INSERT OR IGNORE，幂等）——重跑不冲突，全轨幂等的记账基础。"""
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, _now()),
    )


# -- 公共入口 --

def init_db_ext(db_path: Path) -> None:
    """存量库幂等升级编排器（唯一公共入口，重复调用安全）。

    编排序：init_db（ORM 元数据建表，抢先满足 CREATE IF NOT EXISTS）
    → schema_version 记账 → 逐版本 runner 执行并记账。幂等由三重
    机制保证：PRAGMA guard（列补齐）、IF NOT EXISTS（建表）、
    OR IGNORE（版本记账）。
    """
    # 1. 先确保核心表存在（ORM 元数据建表）
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

        # 3. 版本 1（建表轨）恒记账
        if 1 not in applied:
            _record_version(conn, 1)

        # 4. 数据驱动的 ALTER/数据迁移轨（版本 5-26）：表驱动替代硬编码
        # 串行步骤——runner 在全部 helper 定义后内联成表，逐版本执行并
        # 记账；各 runner 幂等（PRAGMA table_info guard）。
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
            # 24/25 双挂同函数（引入时即无 25 独立内容——见该函数
            # docstring 的历史说明；25 已持久化进 schema_version 不可回收）
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
# 策略数据迁移（版本 19/20）——失败降级不阻塞升级是本族关键契约
# ===================================================================

def _dedup_strategies(conn: sqlite3.Connection) -> None:
    """migration 019：同 gene_signature 策略去重，保留最高分一条。

    保留序：best_score 降序、NULL 分殿后（CASE 护位）。
    错误语义：整体 try/except 降级 logger.warning（exc_info 留痕）
    不中断迁移——数据迁移失败不阻塞升级。
    """
    try:
        # 找重复：gene_signature 出现多于一次
        dup_sigs = conn.execute(
            """SELECT gene_signature FROM strategy
               WHERE gene_signature IS NOT NULL
               GROUP BY gene_signature
               HAVING COUNT(*) > 1"""
        ).fetchall()
        if not dup_sigs:
            return

        for (sig,) in dup_sigs:
            # 该签名全部行，按 best_score 降序、NULL 分殿后
            rows = conn.execute(
                """SELECT strategy_id, best_score FROM strategy
                   WHERE gene_signature = ?
                   ORDER BY CASE WHEN best_score IS NULL THEN 1 ELSE 0 END, best_score DESC""",
                (sig,),
            ).fetchall()
            # 保留首行（最高分），其余删除
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
    """migration 020：全量重算策略名（MTF 层数据纳入 hash）。

    错误语义：整体降级 logger.warning 不中断；行级解析失败 continue
    跳过该行（残留旧名，不阻塞其余行）。
    """
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
                # 单行失败跳过——残留旧名不阻塞其余行
                continue
        if updated:
            logger.info("Recomputed %d strategy names", updated)
    except Exception:
        logger.warning("Strategy name recomputation failed", exc_info=True)
# ===================================================================

def _create_paper_trading_tables(conn: sqlite3.Connection) -> None:
    """建 paper_trading_task 与 paper_trade 双表+索引（幂等）。

    含 B1.3 幂等迁移：paper_trade 同 (task_id, bar_time, action) 唯一
    索引（存量重复先去重保留最早一条——见函数体内中文注释）。新库
    同样被 ORM 抢先满足，本函数对历史库生效。
    """
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
            -- 持仓状态（恢复用）
            position_side     TEXT,
            position_entry    REAL,
            position_quantity REAL,
            position_margin   REAL,
            position_funding  REAL DEFAULT 0,
            -- 账户状态
            balance          REAL,
            unrealized_pnl   REAL DEFAULT 0,
            -- 交易统计
            total_trades     INTEGER DEFAULT 0,
            total_pnl        REAL DEFAULT 0,
            win_count        INTEGER DEFAULT 0,
            loss_count       INTEGER DEFAULT 0,
            -- 最近处理 bar
            last_bar_time    TEXT,
            last_bar_close   REAL,
            -- 心跳
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
    """paper_trading_task 补 execution_model 列（幂等；契约同列补齐族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "execution_model" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN execution_model TEXT DEFAULT 'v1'"
        )


def _apply_bars_held_column(conn: sqlite3.Connection) -> None:
    """paper_trading_task 补 bars_held 列（幂等；契约同列补齐族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "bars_held" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN bars_held INTEGER DEFAULT 0"
        )


def _apply_position_open_cost_column(conn: sqlite3.Connection) -> None:
    """paper_trading_task 补 position_open_cost 列（幂等；契约同列补齐族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "position_open_cost" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN position_open_cost REAL DEFAULT 0"
        )


def _apply_pending_decision_column(conn: sqlite3.Connection) -> None:
    """paper_trading_task 补 pending_decision_json 列（幂等；契约同列补齐族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "pending_decision_json" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN pending_decision_json TEXT"
        )


def _apply_confidence_sizing_column(conn: sqlite3.Connection) -> None:
    """paper_trading_task 补 confidence_sizing_enabled 列（幂等；契约同列补齐族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "confidence_sizing_enabled" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN confidence_sizing_enabled INTEGER DEFAULT 0"
        )


def _apply_prediction_dna_column(conn: sqlite3.Connection) -> None:
    """paper_trading_task 补 prediction_dna_json 列（幂等；契约同列补齐族）。"""
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("PRAGMA table_info(paper_trading_task)")
    existing = {row[1] for row in cursor.fetchall()}
    conn.row_factory = None
    if "prediction_dna_json" not in existing:
        conn.execute(
            "ALTER TABLE paper_trading_task ADD COLUMN prediction_dna_json TEXT"
        )


def _create_equity_snapshot_table(conn: sqlite3.Connection) -> None:
    """建 paper_equity_snapshot 表+唯一索引（幂等）。

    idx_equity_task_time 为 (task_id, timestamp) 唯一约束——
    save_equity_snapshots 的 OR IGNORE 幂等依赖（CARD-01 收口后
    ORM models 与 Alembic 轨同构唯一；新库被 ORM 抢先满足）。
    """
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


# -- 模拟交易任务 CRUD（迁 repository/paper_trading.py）--

# ===================================================================
# Live Trading Task（B3c.1 收编：原 core/live_trading/persistence.py）
# ===================================================================

def _create_live_trading_tables(conn: sqlite3.Connection) -> None:
    """建 live_trading_task 表（CREATE IF NOT EXISTS，幂等，收编自 live 轨）。"""
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
