"""ORM 模型全量声明（B3a.1）。

权威源：init_db_ext 终态库 + live 轨 quant.db 的 PRAGMA 实测（2026-09-16，
生成脚本驱动机械映射，保证与存量列级一致）。契约：
- 类型映射 TEXT→Text / REAL→REAL / INTEGER→Integer（渲染回 SQLite DDL
  与存量 PRAGMA 全等，3.2 baseline 对照的前提）
- server_default 保持存量字面量；n_workers 列如实声明（存量库在列，
  代码不再读写，B2 裁决），列删除属未来显式迁移决策
- schema_version 不声明（SQL 轨版本表，Alembic 接管后废弃）
- 二级索引在各类 __table_args__ 显式声明（含 B1.3 幂等唯一索引）
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import REAL, ForeignKey, Index, Text, Integer, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全项目 ORM 声明基类（SQLAlchemy 2.0 风格）。"""


class BacktestResult(Base):
    __tablename__ = "backtest_result"
    __table_args__ = ()
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    result_id: Mapped[str] = mapped_column(Text, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(Text, ForeignKey("strategy.strategy_id"), )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    data_start: Mapped[str] = mapped_column(Text, nullable=False)
    data_end: Mapped[str] = mapped_column(Text, nullable=False)
    init_cash: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('100000'))
    fee: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.001'))
    slippage: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.0005'))
    total_return: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0'))
    sharpe_ratio: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0'))
    max_drawdown: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0'))
    win_rate: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0'))
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    total_score: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0'))
    template_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'profit_first'"))
    dimension_scores: Mapped[Optional[str]] = mapped_column(Text)
    wf_score: Mapped[Optional[float]] = mapped_column(REAL)
    wf_rounds: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    equity_curve: Mapped[Optional[str]] = mapped_column(Text)
    trades_json: Mapped[Optional[str]] = mapped_column(Text)
    run_source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'lab'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    fitness: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0.0'))
    qualified: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    satisfaction_json: Mapped[Optional[str]] = mapped_column(Text)
    session_id: Mapped[Optional[str]] = mapped_column(Text)

class DatasetMeta(Base):
    __tablename__ = "dataset_meta"
    __table_args__ = ()
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    dataset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(Text, nullable=False)
    parquet_path: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    time_start: Mapped[Optional[str]] = mapped_column(Text)
    time_end: Mapped[Optional[str]] = mapped_column(Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'csv_import'"))
    format_detected: Mapped[Optional[str]] = mapped_column(Text)
    timestamp_precision: Mapped[Optional[str]] = mapped_column(Text)
    ohlcv_stats: Mapped[Optional[str]] = mapped_column(Text)
    gap_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    quality_status: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'unknown'"))
    quality_notes: Mapped[Optional[str]] = mapped_column(Text)
    import_batch_id: Mapped[Optional[str]] = mapped_column(Text)
    last_import_at: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

class EvolutionHistory(Base):
    __tablename__ = "evolution_history"
    __table_args__ = ()
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    best_score: Mapped[float] = mapped_column(REAL, nullable=False)
    avg_score: Mapped[float] = mapped_column(REAL, nullable=False)
    top3_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

class EvolutionTask(Base):
    __tablename__ = "evolution_task"
    __table_args__ = ()
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    target_score: Mapped[float] = mapped_column(REAL, nullable=False)
    score_template: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    initial_dna: Mapped[str] = mapped_column(Text, nullable=False)
    champion_dna: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    stop_reason: Mapped[Optional[str]] = mapped_column(Text)
    strategy_threshold: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('80.0'))
    champion_strategy_id: Mapped[Optional[str]] = mapped_column(Text)
    population_size: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('15'))
    max_generations: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('200'))
    elite_ratio: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0.5'))
    current_generation: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    indicator_pool: Mapped[Optional[str]] = mapped_column(Text)
    timeframe_pool: Mapped[Optional[str]] = mapped_column(Text)
    mode: Mapped[Optional[str]] = mapped_column(Text)
    leverage: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('1'))
    direction: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'long'"))
    data_start: Mapped[Optional[str]] = mapped_column(Text)
    data_end: Mapped[Optional[str]] = mapped_column(Text)
    data_time_start: Mapped[Optional[str]] = mapped_column(Text)
    data_time_end: Mapped[Optional[str]] = mapped_column(Text)
    data_row_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    best_score: Mapped[Optional[float]] = mapped_column(REAL)
    champion_metrics: Mapped[Optional[str]] = mapped_column(Text)
    champion_dimension_scores: Mapped[Optional[str]] = mapped_column(Text)
    walk_forward_enabled: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    continuous: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('1'))
    current_phase: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'idle'"))
    progress_json: Mapped[Optional[str]] = mapped_column(Text)
    heartbeat_at: Mapped[Optional[str]] = mapped_column(Text)
    min_annual_return: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0.10'))
    max_drawdown_limit: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0.10'))
    best_fitness: Mapped[Optional[float]] = mapped_column(REAL)
    champion_satisfaction: Mapped[Optional[str]] = mapped_column(Text)
    requirements_json: Mapped[Optional[str]] = mapped_column(Text)
    qualified_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    target_fitness: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('1.0'))
    oos_fitness: Mapped[Optional[float]] = mapped_column(REAL)
    oos_qualified: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    oos_metrics: Mapped[Optional[str]] = mapped_column(Text)

class GenerationSnapshot(Base):
    __tablename__ = "generation_snapshot"
    __table_args__ = ()
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    best_score: Mapped[float] = mapped_column(REAL, nullable=False)
    avg_score: Mapped[float] = mapped_column(REAL, nullable=False)
    best_dna: Mapped[str] = mapped_column(Text, nullable=False)
    population_json: Mapped[str] = mapped_column(Text, nullable=False)
    mutation_log: Mapped[Optional[str]] = mapped_column(Text)
    wf_months: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

class PaperEquitySnapshot(Base):
    __tablename__ = "paper_equity_snapshot"
    __table_args__ = (
        Index("idx_equity_task_time", "task_id", "timestamp"),
        {"sqlite_autoincrement": True},
    )
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False)
    equity: Mapped[float] = mapped_column(REAL, nullable=False)
    balance: Mapped[float] = mapped_column(REAL, nullable=False)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    position_side: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'flat'"))

class PaperTrade(Base):
    __tablename__ = "paper_trade"
    __table_args__ = (
        Index("idx_paper_trade_task", "task_id", "bar_time"),
        Index("idx_paper_trade_unique", "task_id", "bar_time", "action", unique=True),
        {"sqlite_autoincrement": True},
    )
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, ForeignKey("paper_trading_task.task_id"), )
    bar_time: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(REAL, nullable=False)
    quantity: Mapped[float] = mapped_column(REAL, nullable=False)
    pnl: Mapped[Optional[float]] = mapped_column(REAL)
    fee_paid: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    reason: Mapped[Optional[str]] = mapped_column(Text)

class PaperTradingTask(Base):
    __tablename__ = "paper_trading_task"
    __table_args__ = ()
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    strategy_name: Mapped[Optional[str]] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'BTCUSDT'"))
    timeframe: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'4h'"))
    initial_cash: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('100000'))
    fee: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.001'))
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    direction: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'long'"))
    dna_json: Mapped[str] = mapped_column(Text, nullable=False)
    score_template: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'explorer'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[Optional[str]] = mapped_column(Text)
    stopped_at: Mapped[Optional[str]] = mapped_column(Text)
    stop_reason: Mapped[Optional[str]] = mapped_column(Text)
    position_side: Mapped[Optional[str]] = mapped_column(Text)
    position_entry: Mapped[Optional[float]] = mapped_column(REAL)
    position_quantity: Mapped[Optional[float]] = mapped_column(REAL)
    position_margin: Mapped[Optional[float]] = mapped_column(REAL)
    position_funding: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    balance: Mapped[Optional[float]] = mapped_column(REAL)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    total_trades: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    total_pnl: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    win_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    loss_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    last_bar_time: Mapped[Optional[str]] = mapped_column(Text)
    last_bar_close: Mapped[Optional[float]] = mapped_column(REAL)
    heartbeat_at: Mapped[Optional[str]] = mapped_column(Text)
    execution_model: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'v1'"))
    bars_held: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    position_open_cost: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    pending_decision_json: Mapped[Optional[str]] = mapped_column(Text)
    confidence_sizing_enabled: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    prediction_dna_json: Mapped[Optional[str]] = mapped_column(Text)

class Strategy(Base):
    __tablename__ = "strategy"
    __table_args__ = (
        Index("idx_strategy_best_score", text("best_score DESC")),
        Index("idx_strategy_gene_signature", "gene_signature"),
        Index("idx_strategy_source", "source"),
        Index("idx_strategy_source_task_id", "source_task_id"),
    )
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    strategy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    dna_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'"))
    source_task_id: Mapped[Optional[str]] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    best_score: Mapped[Optional[float]] = mapped_column(REAL)
    generation: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    parent_ids: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    gene_signature: Mapped[Optional[str]] = mapped_column(Text)
    metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    best_fitness: Mapped[Optional[float]] = mapped_column(REAL)
    qualified: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('NULL'))
    verify_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    verify_avg_score: Mapped[Optional[float]] = mapped_column(REAL)
    verify_best_score: Mapped[Optional[float]] = mapped_column(REAL)
    last_verified_at: Mapped[Optional[str]] = mapped_column(Text)
    verify_star: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('NULL'))

class VerifySession(Base):
    __tablename__ = "verify_session"
    __table_args__ = (
        Index("idx_verify_session_created", text("created_at DESC")),
    )
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    strategy_ids: Mapped[str] = mapped_column(Text, nullable=False)
    data_ranges: Mapped[str] = mapped_column(Text, nullable=False)
    init_cash: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('100000'))
    fee: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.001'))
    slippage: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.0005'))
    summary_json: Mapped[Optional[str]] = mapped_column(Text)
    total_results: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    total_strategies: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[Optional[str]] = mapped_column(Text)

class LiveTradingTask(Base):
    __tablename__ = "live_trading_task"
    __table_args__ = ()
    # 列结构权威源：data/quant.db PRAGMA（live 轨自建，B3c 收编）
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    dna_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    stop_reason: Mapped[Optional[str]] = mapped_column(Text)
    direction: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'mixed'"))
    position_side: Mapped[Optional[str]] = mapped_column(Text, server_default=text("''"))
    position_qty: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    position_entry: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    position_sl: Mapped[Optional[float]] = mapped_column(REAL)
    position_tp: Mapped[Optional[float]] = mapped_column(REAL)
    balance: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    equity: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    total_trades: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    total_pnl: Mapped[Optional[float]] = mapped_column(REAL, server_default=text('0'))
    win_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    loss_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    last_bar_time: Mapped[Optional[str]] = mapped_column(Text)
    heartbeat_at: Mapped[Optional[str]] = mapped_column(Text)
    config_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)