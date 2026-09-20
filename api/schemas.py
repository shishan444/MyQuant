"""Pydantic V2 request/response models for the API layer."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


# ── Enums ──


class ImportFormat(str, Enum):
    BINANCE_OFFICIAL = "binance_official"
    GENERIC_OHLCV = "generic_ohlcv"


class TimestampPrecision(str, Enum):
    MILLISECOND = "ms"
    MICROSECOND = "us"


class ImportMode(str, Enum):
    MERGE = "merge"
    REPLACE = "replace"
    NEW = "new"


class ConditionType(str, Enum):
    LT = "lt"
    GT = "gt"
    LE = "le"
    GE = "ge"
    CROSS_ABOVE = "cross_above"
    CROSS_BELOW = "cross_below"
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"


class SignalRole(str, Enum):
    ENTRY_TRIGGER = "entry_trigger"
    ENTRY_GUARD = "entry_guard"
    EXIT_TRIGGER = "exit_trigger"
    EXIT_GUARD = "exit_guard"
    ADD_TRIGGER = "add_trigger"
    ADD_GUARD = "add_guard"
    REDUCE_TRIGGER = "reduce_trigger"
    REDUCE_GUARD = "reduce_guard"
    DIRECTION = "direction"


class ScoreTemplate(str, Enum):
    EXPLORER = "explorer"
    OPTIMIZER = "optimizer"
    MAX_RETURN = "max_return"
    # Legacy aliases (auto-mapped in templates.py)
    PROFIT_FIRST = "profit_first"
    STEADY = "steady"
    RISK_FIRST = "risk_first"
    CUSTOM = "custom"


class RequirementsConfigModel(BaseModel):
    """User-configurable requirements for strategy qualification."""
    objective: str = Field(default="sharpe", description="Objective function: sharpe, calmar, or annual_return")
    min_annual_return: float = Field(default=0.0, ge=0.0, description="Minimum annual return constraint (0 = disabled)")
    max_drawdown: float = Field(default=0.30, ge=0.05, le=0.99, description="Maximum drawdown (0.30 = 30%)")
    min_win_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum win rate (0 = disabled)")
    min_total_trades: int = Field(default=10, ge=0, description="Minimum total trades")
    min_profit_factor: float = Field(default=1.2, ge=0.0, description="Minimum profit factor")


# ── DNA Component Models ──


class SignalGeneModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: str
    params: Dict[str, Any]
    role: SignalRole
    field: Optional[str] = Field(None, validation_alias=AliasChoices("field_name", "field"))
    condition: Dict[str, Any] = Field(default_factory=dict)


class LogicGenesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_logic: str = "AND"
    exit_logic: str = "AND"
    add_logic: str = "AND"
    reduce_logic: str = "AND"


class ExecutionGenesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeframe: str = "4h"
    symbol: str = "BTCUSDT"


class RiskGenesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_loss: float = 0.05
    take_profit: Optional[float] = None
    position_size: float = 0.3
    leverage: int = Field(default=1, ge=1, le=10)
    direction: str = Field(default="long", pattern="^(long|short|mixed)$")
    sl_mode: str = Field(default="pct", pattern="^(pct|atr)$")
    atr_period: int = Field(default=14, ge=1)


class DNAModel(BaseModel):
    """Full StrategyDNA as a Pydantic model for API serialization."""

    model_config = ConfigDict(extra="forbid")

    signal_genes: List[SignalGeneModel] = Field(default_factory=list)
    logic_genes: LogicGenesModel = Field(default_factory=LogicGenesModel)
    execution_genes: ExecutionGenesModel = Field(default_factory=ExecutionGenesModel)
    risk_genes: RiskGenesModel = Field(default_factory=RiskGenesModel)
    strategy_id: Optional[str] = None
    generation: int = 0
    parent_ids: List[str] = Field(default_factory=list)
    mutation_ops: List[str] = Field(default_factory=list)
    layers: Optional[List[dict]] = None
    cross_layer_logic: str = "AND"
    mtf_mode: Optional[str] = None
    confluence_threshold: float = 0.3
    proximity_mult: float = 1.5
    # == v2 超集（add-ai-research-mcp 6.1）：承载 signal_sources 与 composition ==
    signal_sources: Optional[List[Dict[str, Any]]] = None
    composition: Optional[Dict[str, Any]] = None


class TimeframeLayerModel(BaseModel):
    """A single timeframe layer within an MTF strategy."""

    model_config = ConfigDict(extra="forbid")

    timeframe: str
    signal_genes: List[SignalGeneModel] = Field(default_factory=list)
    logic_genes: LogicGenesModel = Field(default_factory=LogicGenesModel)
    role: Optional[str] = None  # "structure" | "zone" | "execution"


# ── Strategy Schemas ──


class StrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    dna: DNAModel
    symbol: str
    timeframe: str
    source: str = "manual"
    source_task_id: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None


class StrategyMetrics(BaseModel):
    annual_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    total_trades: Optional[int] = None
    profit_factor: Optional[float] = None


class StrategyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy_id: str
    name: Optional[str] = None
    dna: Optional[DNAModel] = None
    symbol: str
    timeframe: str
    source: str = "manual"
    source_task_id: Optional[str] = None
    gene_signature: Optional[str] = None
    status: Optional[str] = None  # draft | active | archived
    best_score: Optional[float] = None
    best_fitness: Optional[float] = None
    qualified: Optional[bool] = None
    metrics: Optional[StrategyMetrics] = None
    generation: int = 0
    parent_ids: Optional[str] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    verify_count: int = 0
    verify_avg_score: Optional[float] = None
    verify_best_score: Optional[float] = None
    last_verified_at: Optional[str] = None
    verify_star: Optional[int] = None
    created_at: str
    updated_at: str


class StrategyListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[StrategyResponse]
    total: int


class StrategyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    dna: Optional[DNAModel] = None
    tags: Optional[str] = None
    notes: Optional[str] = None
    best_score: Optional[float] = None
    best_fitness: Optional[float] = None
    qualified: Optional[bool] = None


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: Optional[str] = None
    dna: Optional[DNAModel] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    dataset_id: str
    init_cash: float = 100000.0
    fee: float = 0.001
    slippage: float = 0.0005
    score_template: str = "explorer"
    data_start: Optional[str] = None
    data_end: Optional[str] = None
    timeframe_pool: Optional[List[str]] = None


class BacktestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    result_id: str
    strategy_id: str
    symbol: str
    timeframe: str
    data_start: Optional[str] = None
    data_end: Optional[str] = None
    init_cash: float = 100000.0
    fee: float = 0.001
    slippage: float = 0.0005
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    total_score: float = 0.0
    fitness: float = 0.0
    qualified: bool = False
    template_name: str = "explorer"
    dimension_scores: Optional[Dict[str, Any]] = None
    satisfaction: Optional[Dict[str, Any]] = None
    run_source: str = "lab"
    equity_curve: Optional[List[Dict[str, Any]]] = None
    signals: Optional[List[Dict[str, Any]]] = None
    total_funding_cost: float = 0.0
    liquidated: bool = False


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_ids: List[str]
    dataset_id: str
    init_cash: float = 100000.0
    fee: float = 0.001
    slippage: float = 0.0005
    score_template: str = "explorer"
    data_start: Optional[str] = None
    data_end: Optional[str] = None


class CompareResultItem(BaseModel):
    strategy_id: str
    result_id: Optional[str] = None
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    total_score: float = 0.0
    fitness: float = 0.0
    satisfaction: Optional[Dict[str, Any]] = None
    dimension_scores: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CompareResponse(BaseModel):
    results: List[CompareResultItem]


# ── Verify Schemas ──


class VerifyDateRange(BaseModel):
    start: str
    end: str


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_ids: List[str]
    data_ranges: List[VerifyDateRange]
    init_cash: float = 100000.0
    fee: float = 0.001
    slippage: float = 0.0005
    leverage: int = 1


class VerifyResultItem(BaseModel):
    strategy_id: str
    data_start: str
    data_end: str
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    fitness: float = 0.0
    qualified: bool = False
    error: Optional[str] = None


class VerifyPeriodSummary(BaseModel):
    data_start: str
    data_end: str
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    fitness: float = 0.0
    qualified: bool = False


class VerifySummaryItem(BaseModel):
    strategy_id: str
    strategy_name: str
    comprehensive_score: float = 0.0
    avg_fitness: float = 0.0
    qualified_count: int = 0
    total_periods: int = 0
    per_period_metrics: List[VerifyPeriodSummary] = []


class VerifyResponse(BaseModel):
    results: List[VerifyResultItem]
    summary: List[VerifySummaryItem]


# ── Batch Backtest Schemas ──


class BatchBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_ids: List[str]
    data_ranges: List[VerifyDateRange]
    init_cash: float = 100000.0
    fee: float = 0.001
    slippage: float = 0.0005
    leverage: int = 1


class BatchBacktestResultItem(BaseModel):
    result_id: str
    strategy_id: str
    strategy_name: str = ""
    symbol: str = ""
    timeframe: str = ""
    data_start: str
    data_end: str
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    fitness: float = 0.0
    qualified: bool = False
    liquidated: bool = False
    total_funding_cost: float = 0.0
    error: Optional[str] = None


class BatchBacktestSummaryItem(BaseModel):
    strategy_id: str
    strategy_name: str
    symbol: str = ""
    timeframe: str = ""
    avg_total_return: float = 0.0
    avg_sharpe_ratio: float = 0.0
    worst_max_drawdown: float = 0.0
    avg_fitness: float = 0.0
    qualified_count: int = 0
    total_periods: int = 0
    per_period_results: List[BatchBacktestResultItem] = []


class VerifyHistoryItem(BaseModel):
    result_id: str
    strategy_id: str
    strategy_name: Optional[str] = None
    symbol: str
    timeframe: str
    data_start: str
    data_end: str
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    fitness: float = 0.0
    qualified: int = 0
    created_at: str


class VerifyHistoryResponse(BaseModel):
    items: List[VerifyHistoryItem]
    total: int


class VerifySessionResponse(BaseModel):
    session_id: str
    status: str
    strategy_ids: str
    data_ranges: str
    init_cash: float
    fee: float
    slippage: float
    summary_json: Optional[str] = None
    total_results: int = 0
    total_strategies: int = 0
    error_message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class VerifySessionListResponse(BaseModel):
    items: List[VerifySessionResponse]
    total: int


# ── Evolution Schemas ──


class EvolutionTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_dna: Optional[DNAModel] = None
    symbol: str
    timeframe: str
    target_score: float = 1.0
    score_template: str = "explorer"
    requirements: Optional[RequirementsConfigModel] = None
    population_size: int = 15
    max_generations: int = 200
    elite_ratio: float = 0.5
    indicator_pool: Optional[List[str]] = None
    timeframe_pool: Optional[List[str]] = None
    mode: Optional[str] = None
    leverage: int = Field(default=1, ge=1, le=10)
    direction: str = Field(default="long", pattern="^(long|short|mixed)$")
    data_start: Optional[str] = None
    data_end: Optional[str] = None
    continuous: bool = True
    strategy_threshold: float = Field(default=1.0, description="Fitness threshold for auto-extracting strategies")
    min_annual_return: float = Field(default=0.10, ge=0.0, le=10.0, description="Minimum annual return soft constraint (0.10 = 10%, 6.0 = 600%)")
    max_drawdown_limit: Optional[float] = Field(default=0.10, ge=0.05, le=0.80, description="Max drawdown soft constraint (0.10 = 10%). None=disabled")


class EvolutionTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    status: str
    target_score: float
    score_template: str
    symbol: str
    timeframe: str
    initial_dna: Optional[DNAModel] = None
    champion_dna: Optional[DNAModel] = None
    population_size: int = 15
    max_generations: int = 200
    elite_ratio: float = 0.5
    current_generation: int = 0
    created_at: str
    updated_at: str
    stop_reason: Optional[str] = None
    best_score: Optional[float] = None
    best_fitness: Optional[float] = None
    requirements: Optional[RequirementsConfigModel] = None
    qualified_count: int = 0
    leverage: int = 1
    direction: str = "long"
    data_start: Optional[str] = None
    data_end: Optional[str] = None
    data_time_start: Optional[str] = None
    data_time_end: Optional[str] = None
    data_row_count: int = 0
    indicator_pool: Optional[List[str]] = None
    timeframe_pool: Optional[List[str]] = None
    mode: Optional[str] = None
    champion_metrics: Optional[Dict[str, Any]] = None
    champion_dimension_scores: Optional[Dict[str, Any]] = None
    champion_satisfaction: Optional[Dict[str, Any]] = None
    continuous: bool = True
    strategy_threshold: float = 80.0
    min_annual_return: float = 0.10
    max_drawdown_limit: Optional[float] = None
    strategy_count: int = 0
    exploration_efficiency: float = 0.0
    current_phase: Optional[str] = None
    progress_json: Optional[Dict[str, Any]] = None


class EvolutionTaskListResponse(BaseModel):
    items: List[EvolutionTaskResponse]
    total: int
    page: int = 1
    page_size: int = 20


class EvolutionHistoryRecord(BaseModel):
    generation: int
    best_score: float
    avg_score: float
    best_fitness: Optional[float] = None
    avg_fitness: Optional[float] = None
    top3_summary: Optional[str] = None
    created_at: str


class EvolutionHistoryResponse(BaseModel):
    task_id: str
    generations: List[EvolutionHistoryRecord]


# ── Data Schemas ──


class DataImportResponse(BaseModel):
    dataset_id: str
    symbol: str
    interval: str
    rows_imported: int
    format_detected: str
    timestamp_precision: str
    files_processed: int = 1
    time_range: Optional[List[str]] = None


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dataset_id: str
    symbol: str
    interval: str
    parquet_path: str
    row_count: int = 0
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    file_size_bytes: int = 0
    source: str = "csv_import"
    format_detected: Optional[str] = None
    timestamp_precision: Optional[str] = None
    quality_status: str = "unknown"
    quality_notes: Optional[str] = None
    gap_count: int = 0
    created_at: str
    updated_at: str


class DatasetListResponse(BaseModel):
    items: List[DatasetResponse]
    total: int


class DatasetPreviewResponse(BaseModel):
    dataset_id: str
    total_rows: int
    rows: List[Dict[str, Any]]


class OhlcvResponse(BaseModel):
    dataset_id: str
    data: List[Dict[str, Any]]


# ── Config Schemas ──


class AvailableSource(BaseModel):
    symbol: str
    timeframe: str
    time_start: Optional[str] = None
    time_end: Optional[str] = None


class AvailableSourcesResponse(BaseModel):
    sources: List[AvailableSource]


class ConfigResponse(BaseModel):
    symbols: List[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframes: List[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    backtest: Dict[str, Any] = Field(default_factory=dict)
    evolution: Dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    version: str = "0.9.0"
    timestamp: str = ""


# ── Paper Trading Models ──


class PaperTradingTaskCreate(BaseModel):
    """Request to create a paper trading task."""
    dna_json: str = Field(..., description="StrategyDNA as JSON string")
    symbol: str = Field(default="BTCUSDT")
    timeframe: str = Field(default="4h")
    initial_cash: float = Field(default=100_000, gt=0)
    fee: float = Field(default=0.001, ge=0)
    leverage: int = Field(default=1, ge=1, le=10)
    direction: str = Field(default="long", pattern="^(long|short|mixed)$")
    score_template: str = Field(default="explorer")
    strategy_name: Optional[str] = None
    strategy_id: Optional[str] = Field(default=None, description="Optional strategy ID for qualified check")
    confidence_sizing_enabled: bool = False
    prediction_dna_json: Optional[str] = Field(default=None, description="PredictionDNA as JSON string")


class PaperTradingTaskResponse(BaseModel):
    task_id: str
    status: str
    strategy_name: Optional[str] = None
    symbol: str
    timeframe: str
    initial_cash: float
    fee: float
    leverage: int
    direction: str
    score_template: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    stop_reason: Optional[str] = None
    # Position
    position_side: Optional[str] = None
    position_entry: Optional[float] = None
    position_quantity: Optional[float] = None
    position_margin: Optional[float] = None
    position_funding: Optional[float] = None
    # Account
    balance: Optional[float] = None
    unrealized_pnl: float = 0.0
    # Stats
    total_trades: int = 0
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    # Last bar
    last_bar_time: Optional[str] = None
    last_bar_close: Optional[float] = None
    # Execution model
    execution_model: str = "v1"
    confidence_sizing_enabled: bool = False
    prediction_dna_json: Optional[str] = None
    # Warnings
    warnings: Optional[List[str]] = None


class PaperTradingTaskListResponse(BaseModel):
    tasks: List[PaperTradingTaskResponse]
    total: int


class PaperTradeResponse(BaseModel):
    id: int
    task_id: str
    bar_time: str
    side: str
    action: str
    price: float
    quantity: float
    pnl: Optional[float] = None
    fee_paid: float = 0.0
    reason: Optional[str] = None


class PaperTradeListResponse(BaseModel):
    trades: List[PaperTradeResponse]
    total: int


class EquitySnapshotResponse(BaseModel):
    id: int
    task_id: str
    timestamp: str
    equity: float
    balance: float
    unrealized_pnl: float = 0.0
    position_side: str = "flat"


class EquitySnapshotListResponse(BaseModel):
    snapshots: List[EquitySnapshotResponse]
    total: int


class TradingMetricsResponse(BaseModel):
    task_id: str
    total_return: float = 0.0
    total_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_trade_pnl: float = 0.0
    total_trades: int = 0
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0


# ---------------------------------------------------------------------------
# Live Trading (demo mode) — 第8步 API schemas
# ---------------------------------------------------------------------------


class LiveTradingTaskCreate(BaseModel):
    symbol: str
    timeframe: str
    dna_json: str
    direction: str = "mixed"
    config_json: Optional[str] = None


class LiveTradingTaskUpdate(BaseModel):
    status: Optional[str] = None
    config_json: Optional[str] = None
    stop_reason: Optional[str] = None


class LiveTradingTaskResponse(BaseModel):
    task_id: str
    symbol: str
    timeframe: str
    status: str
    config_json: Optional[str] = None
    direction: str = "mixed"
    position_side: str = ""
    position_qty: float = 0.0
    position_entry: float = 0.0
    balance: float = 0.0
    equity: float = 0.0
    unrealized_pnl: float = 0.0
    total_trades: int = 0
    total_pnl: float = 0.0
    last_bar_time: Optional[str] = None
    heartbeat_at: Optional[str] = None
    created_at: str
    updated_at: str


class LiveTradingTaskListResponse(BaseModel):
    tasks: List[LiveTradingTaskResponse]
    total: int


# == research 域（add-ai-research-mcp 3.x）==


class ConditionItem(BaseModel):
    """假设条件（与假设验证引擎 {subject, action, target} 同源形制）。"""

    subject: str
    action: str
    target: Union[float, str]


class HypothesisConditions(BaseModel):
    """WHEN/THEN 结构化条件（机器判定唯一来源；陈述仅展示）。"""

    when_conditions: List[ConditionItem]
    then_conditions: List[ConditionItem]


class DataRangeItem(BaseModel):
    """半开区间 [start, end)，UTC ISO8601。"""

    symbol: str
    timeframe: str
    start: str
    end: str


class HypothesisCreate(BaseModel):
    conditions: HypothesisConditions
    statement: str
    data_ranges: List[DataRangeItem]


class HypothesisTransitionRequest(BaseModel):
    target_status: str  # supported | refuted | superseded | abandoned
    evidence_ref: str  # 验证 record_id / 接替假设 id / abandoned 时为放弃说明


class HypothesisResponse(BaseModel):
    hypothesis_id: str
    status: str = "active"
    duplicated: bool = False


class HypothesisListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    page: int = 1
    page_size: int = 50


class IsolatedBacktestRequest(BaseModel):
    hypothesis_id: str
    strategy_id: str
    request_range: DataRangeItem
    threshold_profile: Optional[Dict[str, Any]] = None


class RandomWindowRequest(BaseModel):
    strategy_id: str
    symbol: str
    timeframe: str
    data_start: str
    data_end: str
    window_bars: int = 200
    n_windows: int = 8
    seed: int = 42
    hypothesis_id: Optional[str] = None


class AnalysisRunRequest(BaseModel):
    """五类模板统一入口（附.4）。"""

    template: str  # condition_stats|distribution_compare|correlation_scan|event_study|pattern_mining
    params: Dict[str, Any]
    data_range: DataRangeItem
    hypothesis_id: Optional[str] = None


class FeatureRequestCreate(BaseModel):
    title: str
    payload: Dict[str, Any] = {}


class FeatureRequestResponse(BaseModel):
    request_id: str
    title: str
    status: str
    created_at: str
