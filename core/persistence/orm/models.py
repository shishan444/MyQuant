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
    """
    回测结果记录：run_source 区分 lab/verify 等通道，fitness/qualified 为
    021 评分制度列。
    """
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
    """
    数据集元数据与质量档案：行列/时间界/质量状态（quality_status），
    数据检视与装载的登记面。
    """
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
    """
    代际轻量分数记录：UI 图表用（best/avg/top3），不带种群。
    """
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
    """
    进化任务主记录：状态机（status/stop_reason/current_phase）+
    champion 全套（dna/metrics/fitness）+OOS 验证+配置列群。
    """
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
    """
    全种群快照：checkpoint resume 载体（population_json 整代持久，
    复合主键 task_id+generation 幂等）。
    """
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
    """
    权益快照：(task_id, timestamp) 唯一索引是 OR IGNORE 幂等键
    （CARD-01 收口——models 与 Alembic 两轨同构唯一，TD-10 换芯后
    Alembic 链为版本演进唯一权威）。
    """
    __tablename__ = "paper_equity_snapshot"
    __table_args__ = (
        # (task_id, timestamp) 唯一：save_equity_snapshots 的 OR IGNORE
        # 幂等依赖此约束（Alembic 04b595687d2f 同规收口）
        Index("idx_equity_task_time", "task_id", "timestamp", unique=True),
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
    """
    模拟成交记录：B1.3 (task_id, bar_time, action) 唯一——bar 游标
    重放不重复落库。
    """
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
    """
    模拟盘任务：持仓/账户/统计三类运行状态+execution_model 执行
    形制+resume 恢复列群。
    """
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
    """
    策略库：gene_signature 去重身份+draft/active/archived 生命周期
    （M4）+verify 滚动统计五列。
    """
    __tablename__ = "strategy"
    __table_args__ = (
        # 普通列索引（原 text("best_score DESC") 表达式形）：SQLite 反射
        # 不解析索引方向、DESC 与 ASC 查询等价（方向仅存储声明），表达式
        # 形在 autogenerate 对照下恒判漂移（baseline 遗漏本索引的根因）
        Index("idx_strategy_best_score", "best_score"),
        Index("idx_strategy_gene_signature", "gene_signature"),
        Index("idx_strategy_source", "source"),
        Index("idx_strategy_source_task_id", "source_task_id"),
    )
    # 列结构权威源：init_db_ext 终态库 PRAGMA（2026-09-16）
    strategy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    dna_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 生命周期状态（improve-mcp-agent-loop M4）：draft=假设转化草稿
    # （验证对象、不可直接消费）；active=可消费；archived=归档
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'"))
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
    # qualified：deprecated 死列（build-strategy-lifecycle 5.3 处置）——
    # 声明后从未写入（save_strategy 签名无此参数、_ALLOWED_UPDATE_FIELDS
    # 不含、全库零 UPDATE 写点），实测全 NULL；资格语义已由六态+
    # 凭证接管（research_status 三序序③兜底读冻结值）。保留不删：
    # 过滤面连带无收益+降级路径复杂化；同名异列注意——
    # backtest_result.qualified 是真数据活列，与本列无关。
    qualified: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('NULL'))
    verify_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('0'))
    verify_avg_score: Mapped[Optional[float]] = mapped_column(REAL)
    verify_best_score: Mapped[Optional[float]] = mapped_column(REAL)
    last_verified_at: Mapped[Optional[str]] = mapped_column(Text)
    verify_star: Mapped[Optional[int]] = mapped_column(Integer, server_default=text('NULL'))

class VerifySession(Base):
    """
    验证会话句柄：批量验证的运行壳（status/summary/错误信息），
    结果行在 backtest_result.session_id 关联。
    """
    __tablename__ = "verify_session"
    __table_args__ = (
        # 同 Strategy.idx_strategy_best_score：普通列形（反射/autogenerate 一致）
        Index("idx_verify_session_created", "created_at"),
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
    """
    实盘任务（demo mode）：持仓/余额/心跳运行状态，bar 轮询 runner
    的持久化面（B3c.1 收编自 live 轨）。
    """
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

# == research 域（8 表，add-ai-research-mcp 1.2）==
# 字段级权威源：openspec 变更 design.md 附.1；时间戳列沿用工程 TEXT ISO 形制；
# JSON 列以 Text 存 JSON 字符串（strategy.dna_json 先例）；
# 布尔以 Integer 0/1 表达（SQLite 惯例，无原生 BOOLEAN）。


class ResearchHypothesis(Base):
    """研究假设：conditions（WHEN/THEN 结构化 JSON）是验证判定的唯一机器来源。"""

    __tablename__ = "research_hypotheses"
    __table_args__ = (
        # 幂等键：同条件集+同区间集且 active 视为同一假设（附.5 卡1）
        Index(
            "ux_hypotheses_active_identity",
            "conditions_hash", "ranges_hash",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )
    hypothesis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conditions: Mapped[str] = mapped_column(Text, nullable=False)
    conditions_hash: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    data_ranges: Mapped[str] = mapped_column(Text, nullable=False)
    ranges_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # active -> supported | refuted | superseded（迁移必引证据，终态拒新验证）
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    terminal_ref: Mapped[Optional[str]] = mapped_column(Text)
    superseded_by: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    # 策略层参数声明块（0.4.2 R1，JSON 串）：NULL=无声明（翻译走平台
    # 默认）；不进 conditions/不进 conditions_hash（不参与身份指纹，
    # 参数变体经终态后重登记产生——strategy-param-declaration 规格）
    strategy_params: Mapped[Optional[str]] = mapped_column(Text)


class ResearchLog(Base):
    """研究日志（追加不可变）：强制自动记录分析区间，拦截漏报（spec 隔离判定）。"""

    __tablename__ = "research_log"
    __table_args__ = (
        Index("ix_research_log_hypothesis", "hypothesis_id", "created_at"),
    )
    log_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hypothesis_id: Mapped[Optional[str]] = mapped_column(Text)
    # register | analysis | validate | transition | plugin
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[Optional[str]] = mapped_column(Text)
    data_ranges: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisTask(Base):
    """研究长任务句柄（线程池执行器产物，D3/D11 轮询语义）。"""

    __tablename__ = "analysis_tasks"
    __table_args__ = (
        Index("ix_analysis_tasks_status", "status", "created_at"),
    )
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)  # ant_ 前缀
    template: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    # pending | running | succeeded | failed（timeout/interrupted 为 failed 的 reason）
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    result_ref: Mapped[Optional[str]] = mapped_column(Text)
    # 截断标记（变更三 8.1）：1=结果超 20 万字符上限被截断（轮询侧
    # 两态披露依据——截断 vs 畸形诚实区分）；0=未截断（存量行 server
    # default 0 自动归位，语义自洽无回填）
    result_truncated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("'0'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[Optional[str]] = mapped_column(Text)


class FormulaPlugin(Base):
    """公式插件（受限 DSL 原文 + 版本化）。"""

    __tablename__ = "formula_plugins"
    __table_args__ = (
        Index("ux_formula_plugins_name_version", "name", "version", unique=True),
    )
    plugin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    output_columns: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    # valid | rejected（rejected 只留记录不注册）
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'valid'"))
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    strategy_visible: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ModelPlugin(Base):
    """模型插件（参数化模板配置 + 样本外摘要缓存；卡5 状态机 pending→valid|rejected）。"""

    __tablename__ = "model_plugins"
    __table_args__ = (
        Index("ux_model_plugins_name_version", "name", "version", unique=True),
    )
    plugin_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    config: Mapped[str] = mapped_column(Text, nullable=False)  # JSON{features,template,params,target,horizon}
    signal_column: Mapped[str] = mapped_column(Text, nullable=False)
    # pending | valid | rejected（异步训练：failed 训练/超时 → rejected+reason，卡5）
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    oos_summary: Mapped[Optional[str]] = mapped_column(Text)  # JSON 缓存（权威在 validation_records）
    strategy_visible: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class SignalRegistryEntry(Base):
    """信号注册表（仅插件行；内置指标不落库——运行时与 INDICATOR_REGISTRY 合并，D4 单源纪律）。"""

    __tablename__ = "signal_registry"
    __table_args__ = (
        Index("ux_signal_registry_column_version", "column_name", "version", unique=True),
    )
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # formula | model
    source: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_id: Mapped[str] = mapped_column(Text, nullable=False)
    column_name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    strategy_visible: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class FeatureRequest(Base):
    """慢通道功能需求单：只保证持久化与可查，不自动实现（能力自述预期管理）。"""

    __tablename__ = "feature_requests"
    __table_args__ = (
        Index("ix_feature_requests_status", "status", "created_at"),
    )
    request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    # 回销出处载体（变更二 4.1）：结构化 JSON {source, note, at, by}——
    # 工程侧写入域，与提单方 payload 分域（权威单源：两个写入者不共域）；
    # 存量行 null=未回销，语义自洽无回填
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ValidationRecord(Base):
    """验证证据记录（含组合策略分量证据清单）。"""

    __tablename__ = "validation_records"
    __table_args__ = (
        # 幂等键（0.3.3 G7 五元化）：三元组 + level（类型隔离——隔离/
        # 分段/随机窗互不命中）+ segmentation（分段身份：auto 字面量 /
        # explicit 段清单内容摘要 / 其余空串——同区间不同分段方案各自
        # 独立；NOT NULL 空串判重——NULL 不判重会丢非分段记录的库级
        # 幂等保护）
        Index(
            "ux_validation_idem",
            "hypothesis_id", "strategy_id", "range_hash",
            "level", "segmentation",
            unique=True,
        ),
        Index("ix_validation_hypothesis", "hypothesis_id", "created_at"),
    )
    record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hypothesis_id: Mapped[str] = mapped_column(
        Text, ForeignKey("research_hypotheses.hypothesis_id"))
    strategy_id: Mapped[Optional[str]] = mapped_column(Text)
    # isolated_backtest | random_window | composite | component
    level: Mapped[str] = mapped_column(Text, nullable=False)
    # 分段身份（0.3.3 G7 幂等第五维——见 _segmentation_identity）
    segmentation: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="")
    params: Mapped[str] = mapped_column(Text, nullable=False)  # JSON（含 warmup、seed）
    result: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    # pass | fail | inconclusive（预定义门槛表三段闸门判定——spec
    # research-validation-protocol；inconclusive=样本不足或 95% CI
    # 跨零，证据不足非证伪——thresholds.py）
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    range_hash: Mapped[str] = mapped_column(Text, nullable=False)
    components: Mapped[Optional[str]] = mapped_column(Text)  # JSON 分量证据 record_id 清单
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# == paper trading 域（5 表，rebuild-paper-trading 2.1）==
# 字段级权威源：openspec 变更 rebuild-paper-trading design.md §4.1-4.5；
# 时间戳列沿工程 TEXT ISO 形制；JSON 列以 Text 存 JSON 字符串
# （strategy.dna_json 先例）。唯一键在 __table_args__ 与 Alembic 迁移
# 两处同步声明（两方不一致静默架空——验证须实库 PRAGMA index_list）。
# 旧三表（paper_trading_task/paper_trade/paper_equity_snapshot）留档
# 不删不改（组 7 退役旧仓库，表数据保留）。


class PtTask(Base):
    """
    纸盘任务主表（运行态权威）：五态状态机（pending/running/
    completed/stopped/error）+账户/持仓/统计三组运行状态+恢复游标
    （last_bar_time）+创建时快照组（dna/research_status——任务自包含）。
    """

    __tablename__ = "pt_task"
    __table_args__ = ()
    # task_id：uuid4 hex 12 位沿旧形制（api/routes/trading.py:111 同源惯例）
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)  # 上盘来源引用
    dna_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)  # 创建时 DNA 快照（D-6）
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    # 二值从 DNA execution_genes 抄录（查询效率列，不独立配置）
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    initial_cash: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('100000'))
    fee: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.001'))
    slippage: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0.0005'))
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))  # 覆写值，缺省取 DNA
    window_bars: Mapped[Optional[int]] = mapped_column(Integer)  # NULL=不限期不自动结题
    stop_loss_pct: Mapped[Optional[float]] = mapped_column(REAL)  # NULL=无停止条件
    bars_processed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))  # 窗口游标
    last_bar_time: Mapped[Optional[str]] = mapped_column(Text)  # 恢复游标=最后处理完的 bar
    # 账户运行态组（组完整性=全 NULL 或全非 NULL——恢复校验判据）
    balance: Mapped[Optional[float]] = mapped_column(REAL)
    position_side: Mapped[Optional[str]] = mapped_column(Text)
    position_entry: Mapped[Optional[float]] = mapped_column(REAL)
    position_quantity: Mapped[Optional[float]] = mapped_column(REAL)
    position_margin: Mapped[Optional[float]] = mapped_column(REAL)
    position_open_cost: Mapped[Optional[float]] = mapped_column(REAL)
    position_sl_price: Mapped[Optional[float]] = mapped_column(REAL)
    position_tp_price: Mapped[Optional[float]] = mapped_column(REAL)
    position_trail_peak: Mapped[Optional[float]] = mapped_column(REAL)
    position_entry_bar_seq: Mapped[Optional[int]] = mapped_column(Integer)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(REAL)
    # bar N−1 所产待执行意图（次根开盘执行）
    pending_intent_json: Mapped[Optional[str]] = mapped_column(Text)
    # 统计组
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    total_pnl: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text('0'))
    # 创建时研究状态快照（§9.2 四值：draft/unverified/verified/
    # verification_failed）。创建时点审计+查询期现算双句式
    # （build-strategy-lifecycle 5.3）：本列=创建时点审计事实（创建/
    # clone 时写入，与 dna_snapshot_json 同构的快照组）；查询面返回
    # research_status=查询期现算值（resolve_research_status 三序，
    # 迁移后随动）——不同名事实不构成双源。
    research_status_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[Optional[str]] = mapped_column(Text)
    stopped_at: Mapped[Optional[str]] = mapped_column(Text)
    stop_reason: Mapped[Optional[str]] = mapped_column(Text)
    heartbeat_at: Mapped[Optional[str]] = mapped_column(Text)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)


class PtFill(Base):
    """
    纸盘成交流水（成交流水权威+审计依据）：seq 按 design §3.5 八步序
    编号——「为什么这根 K 线做了这个动作」的审计锚。
    """

    __tablename__ = "pt_fill"
    __table_args__ = (
        # (task_id, bar_time, seq) 唯一：恢复重放幂等键（INSERT OR IGNORE）
        Index("ux_pt_fill_task_bar_seq", "task_id", "bar_time", "seq", unique=True),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, ForeignKey("pt_task.task_id"))
    bar_time: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # 九值同 Intent.kind（open/close/add/reduce/exit_sl/exit_tp/
    # exit_max_hold/exit_time/exit_liquidation）
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[Optional[float]] = mapped_column(REAL)
    quantity: Mapped[Optional[float]] = mapped_column(REAL)
    fee: Mapped[Optional[float]] = mapped_column(REAL)
    realized_pnl: Mapped[Optional[float]] = mapped_column(REAL)  # 仅出场类
    reason: Mapped[Optional[str]] = mapped_column(Text)


class PtEquity(Base):
    """
    纸盘权益快照（权益权威）：每 bar 收盘估值一行，append-only
    （Q-2 挂起补记不回改历史快照）。
    """

    __tablename__ = "pt_equity"
    __table_args__ = (
        # (task_id, bar_time) 唯一：幂等键（INSERT OR IGNORE）
        Index("ux_pt_equity_task_bar", "task_id", "bar_time", unique=True),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, ForeignKey("pt_task.task_id"))
    bar_time: Mapped[str] = mapped_column(Text, nullable=False)
    equity: Mapped[float] = mapped_column(REAL, nullable=False)
    balance: Mapped[float] = mapped_column(REAL, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(REAL, nullable=False)
    # 空仓快照为 NULL（不引入 'flat' 假状态——与 pt_task 账户组
    # 全 NULL 语义一致）
    position_side: Mapped[Optional[str]] = mapped_column(Text)
    cumulative_funding: Mapped[float] = mapped_column(REAL, nullable=False)


class PtFunding(Base):
    """
    纸盘资金费明细（资金费权威）：一结算点一行（唯一键吸收补记
    回填）。Q-2 三态：settled=正常/运行中补记已扣款｜pending=挂起
    （rate/amount NULL+快照）｜settled_disclosure_only=终态后回填
    未扣款（终态封账 append-only）。
    """

    __tablename__ = "pt_funding"
    __table_args__ = (
        # (task_id, settle_time) 唯一：一结算点一行，补记回填不产生新行
        Index("ux_pt_funding_task_settle", "task_id", "settle_time", unique=True),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, ForeignKey("pt_task.task_id"))
    settle_time: Mapped[str] = mapped_column(Text, nullable=False)  # 8h 结算点时刻
    bar_time: Mapped[str] = mapped_column(Text, nullable=False)  # 承载该结算的 bar
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'settled'"))
    rate: Mapped[Optional[float]] = mapped_column(REAL)  # settled 行真实费率；pending 行 NULL
    amount: Mapped[Optional[float]] = mapped_column(REAL)  # 正=付出/负=收到；pending 行 NULL
    position_quantity: Mapped[float] = mapped_column(REAL, nullable=False)  # 结算/挂起时仓位快照
    settle_price: Mapped[float] = mapped_column(REAL, nullable=False)  # 承载 bar close，补记金额计算用


class PtReport(Base):
    """
    纸盘结题报告（派生物视图，非第二事实源）：复合 PK (task_id,
    report_version)。结题固化初版 version=1；终态后补记触发修订版
    恒 version=2 重算覆盖（补记历史权威在 pt_funding 行）。
    """

    __tablename__ = "pt_report"
    __table_args__ = ()
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    report_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[str] = mapped_column(Text, nullable=False)  # 重算时点
    report_json: Mapped[str] = mapped_column(Text, nullable=False)  # 完整报告 JSON


# == strategy lifecycle 域（1 表，build-strategy-lifecycle 1.2）==
# 字段级权威源：openspec 变更 build-strategy-lifecycle design.md §3.1；
# 与 Alembic 迁移 de92805ed44c 两处同步声明（两方不一致静默架空——
# 索引验证须实库 PRAGMA index_list，工程坑位）。值域权威源在
# core/strategy/lifecycle/machine.py（六态/九 trigger），表列不设
# CHECK——域守卫由双触发器+写面收敛承担。
class StrategyTransitionEvent(Base):
    """
    策略状态迁移事件（迁移审计单一事实源）：append-only——repository
    只提供 insert+query，无 update/delete 通道；存量分类不产生事件
    （纯净性原则：事件表只记真实业务迁移）。
    """

    __tablename__ = "strategy_transition_event"
    __table_args__ = (
        # 水位推导与按策略查事件史的主查询路径（strategy_id, created_at）
        Index("idx_strategy_transition_event_strategy_created",
              "strategy_id", "created_at"),
    )
    event_id: Mapped[str] = mapped_column(Text, primary_key=True)  # uuid4 hex12
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str] = mapped_column(Text, nullable=False)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[Optional[str]] = mapped_column(Text)  # 凭证载荷（JSON 字符串，可空=manual 边）
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
