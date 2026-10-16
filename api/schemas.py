"""REST 契约的单一事实源（Pydantic V2 请求/响应模型层）。

职能：定义 /api/* 端点的请求与响应形制——FastAPI 据此做传输校验；
OpenAPI 生成类型外化至 web 前端（web/src/types/generated）；MCP
服务经 httpx 运行时消费同一 REST 契约（非类型下游）。
服务对象：全部路由模块（请求校验与响应序列化）+ OpenAPI 文档消费方。
职责边界：只描述传输契约，不含业务逻辑与持久化形制（后者在 core 层）。
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (AliasChoices, BaseModel, ConfigDict, Field,
                      field_validator, model_validator)

# 值域单源引用（D4 家族/0.4.2 值域单源化）：sl_mode 取值域与杠杆红线
# 的权威常量在 core 层，传输层只派生消费——改值单点生效，禁字面重复
from core.strategy.dna import SL_MODES
from core.strategy.validator import MAX_LEVERAGE

# sl_mode pattern 由 SL_MODES 派生（封闭枚举单源——取值域扩展时
# 此处自动跟随，无需改传输层）
_SL_MODE_PATTERN = "^(" + "|".join(SL_MODES) + ")$"


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


# 评分 objective 合法值域（schema 收紧）：权威源为 core/scoring/scorer.py
# 的 _VALID_OBJECTIVES，此处为字面量镜像（Literal 须静态字面量，传输层
# 不跨层引用 core 私有名），漂移由特征测试锚定
ScoringObjective = Literal["sharpe", "calmar", "annual_return"]

# 评分模板名合法值域：权威源为 scorer.py score_strategy 的核心名
# （explorer/optimizer/max_return）+ _ALIASES 别名键集，非法值 422 拒绝
ScoreTemplateName = Literal[
    "explorer", "optimizer", "max_return",
    "balanced", "steady", "custom",
    "aggressive", "profit_first", "conservative", "risk_first",
]


class RequirementsConfigModel(BaseModel):
    """策略达标线的用户可配置项（合格判定的需求侧输入）。"""
    objective: ScoringObjective = Field(default="sharpe", description="Objective function: sharpe, calmar, or annual_return")
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
    # 基因级周期标记（unblock-research-validation A 段；RiskGenesModel
    # 同步铁律——DNA 加字段 API 模型必须同步）
    timeframe: Optional[str] = None


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
    leverage: int = Field(default=1, ge=1, le=MAX_LEVERAGE)
    direction: str = Field(default="long", pattern="^(long|short|mixed)$")
    sl_mode: str = Field(default="pct", pattern=_SL_MODE_PATTERN)
    atr_period: int = Field(default=14, ge=1)
    # 分批数值参数（0.4.2 R3，DC-15）：pydantic 形态=裸 float Optional
    # ——值域权威在 service 声明块/validator 双点，传输层不加 bound
    exit_fraction: Optional[float] = None
    add_fraction: Optional[float] = None
    max_exposure: Optional[float] = None
    # 时间出场（add-max-hold-exit D-2，security S-1 修复）：与 RiskGenes
    # 同步——漏键会使 extra="forbid" 拒收含 max_hold 的 DNA 直传/回显，
    # 且 crud 读回 ValidationError 被吞 → GET 响应 dna=null（DNA 静默
    # 消失）；值域权威在 validator（传输层不加 bound，三键先例同款）
    max_hold: Optional[int] = None
    # 固定时刻出场（任务 6 同步铁律）：UTC 分钟 0-1439（值域权威在
    # validator——传输层不加 bound）
    time_exit_utc: Optional[int] = None


class DNAModel(BaseModel):
    """核心 StrategyDNA 的传输层完整形制（含 to_dna() 领域转换入口）。"""

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
    # 入场触发策略（trigger-policy 卡5 / D11）：五消费面（evolution
    # initial/champion、策略创建读回、compare、batch、verify）传输键
    entry_trigger_policy: Optional[Dict[str, Any]] = None

    def to_dna(self):
        """转换为核心 StrategyDNA（传输模型→领域对象的单一转换入口）。"""
        from core.strategy.dna import StrategyDNA

        return StrategyDNA.from_dict(self.model_dump())


class TimeframeLayerModel(BaseModel):
    """MTF 策略中的单周期层（层内信号与逻辑独立于执行周期评估）。"""

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
    # DEPRECATED（improve-mcp-agent-loop M5）：评分模板残骸，仅兼容保留；
    # 决定评分语义（objective/硬约束→total_score），不影响收益曲线本身；
    # 非法值 422 拒绝；勿在新调用方依赖
    score_template: ScoreTemplateName = Field(
        "explorer", description="DEPRECATED: 评分模板残骸，仅兼容保留；"
        "决定评分语义（影响 total_score），不影响收益曲线本身；非法值 422 拒绝")
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
    win_rate: Optional[float] = None
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
    # 基准对照（improve-backtest-benchmark-disclosure 2.2）：None=未计算/
    # 无基准（旧存库行不含 → None，向后兼容）。零费基准口径：基准为请求
    # 区间 close 的无杠杆买入持有，不扣资金费/交易费（基准从严）。
    # excess_return 为区间口径（total_return - benchmark_return，短样本
    # 不年化外推）；alpha 仍为年化口径（annual_return - market_annual_return）
    benchmark_return: Optional[float] = None
    excess_return: Optional[float] = None
    alpha: Optional[float] = None
    market_annual_return: Optional[float] = None
    profit_factor: Optional[float] = None


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_ids: List[str]
    dataset_id: str
    init_cash: float = 100000.0
    fee: float = 0.001
    slippage: float = 0.0005
    score_template: ScoreTemplateName = "explorer"
    data_start: Optional[str] = None
    data_end: Optional[str] = None


class CompareResultItem(BaseModel):
    strategy_id: str
    result_id: Optional[str] = None
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: Optional[float] = None
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
    win_rate: Optional[float] = None
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
    win_rate: Optional[float] = None
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
    score_template: ScoreTemplateName = "explorer"
    requirements: Optional[RequirementsConfigModel] = None
    population_size: int = 15
    max_generations: int = 200
    elite_ratio: float = 0.5
    indicator_pool: Optional[List[str]] = None
    timeframe_pool: Optional[List[str]] = None
    mode: Optional[str] = None
    leverage: int = Field(default=1, ge=1, le=MAX_LEVERAGE)
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
    timestamp_precision: Optional[str] = None  # GENERIC 不可探测 → None
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


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str = ""


class ConfigUpdate(BaseModel):
    """PUT /api/config 请求体（部分更新：全字段 Optional，未知键忽略）。"""

    model_config = ConfigDict(extra="ignore")

    language: Optional[str] = None
    timezone: Optional[str] = None
    notify_evolution: Optional[bool] = None
    notify_signal: Optional[bool] = None
    binance_api_key: Optional[str] = None
    binance_secret_key: Optional[str] = None
    binance_connected: Optional[bool] = None
    init_cash: Optional[int] = None
    maker_fee: Optional[float] = None
    taker_fee: Optional[float] = None
    max_positions: Optional[int] = None

    @field_validator("init_cash", "max_positions", mode="before")
    @classmethod
    def _reject_bool_for_int(cls, v):
        """int 字段拒 bool（Pydantic v2 lax 下 True→1 属隐式收窄，显式拒绝）。"""
        if isinstance(v, bool):
            raise ValueError("bool value not accepted for int field")
        return v


# ── Paper Trading Models ──


class PaperTradingTaskCreate(BaseModel):
    """创建模拟盘任务的请求体。"""
    dna_json: str = Field(..., description="StrategyDNA as JSON string")
    symbol: str = Field(default="BTCUSDT")
    timeframe: str = Field(default="4h")
    initial_cash: float = Field(default=100_000, gt=0)
    fee: float = Field(default=0.001, ge=0)
    leverage: int = Field(default=1, ge=1, le=MAX_LEVERAGE)
    direction: str = Field(default="long", pattern="^(long|short|mixed)$")
    score_template: ScoreTemplateName = Field(default="explorer")
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
    # 当前持仓
    position_side: Optional[str] = None
    position_entry: Optional[float] = None
    position_quantity: Optional[float] = None
    position_margin: Optional[float] = None
    position_funding: Optional[float] = None
    # 账户
    balance: Optional[float] = None
    unrealized_pnl: float = 0.0
    # 交易统计
    total_trades: int = 0
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    # 最新 K 线
    last_bar_time: Optional[str] = None
    last_bar_close: Optional[float] = None
    # 执行模型
    execution_model: str = "v1"
    confidence_sizing_enabled: bool = False
    prediction_dna_json: Optional[str] = None
    # 运行告警
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
    win_rate: Optional[float] = None
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
    # 状态机值域对齐 core/live_trading runner 全链（save 缺省 pending/
    # runner 异常 error/paper_trading 同构先例 running/stopped）——
    # 非法状态串原可入库（列表过滤与状态判定消费方失真），入站即拒
    status: Optional[Literal["pending", "running", "stopped", "error"]] = None
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


class IndicatorRef(BaseModel):
    """参数化指标结构化引用（0.2.13 列命名统一——OpenAPI 外化形制）。

    extra=forbid（unblock-research-validation T6）：subject 对象拒绝
    未知键——周期标记 timeframe 是条件顶层键不在 subject 内部
    （RR-015 实测错位层级曾被静默忽略导致 1d/1h 同值）。"""
    model_config = ConfigDict(extra="forbid")

    indicator: str
    params: Dict[str, Any] = {}
    field: Optional[str] = None


class ConditionItem(BaseModel):
    """假设条件（与假设验证引擎 {subject, action, target} 同源形制）。

    subject 混合形制：参数化指标用结构化 IndicatorRef，基础列/插件列用
    字符串精确名（业务校验在 service 层 resolve_column_ref）。
    0.2.14：可选 timeframe 键——仅 when 条件、>= 基准周期的高周期
    （timeframe 修饰整条件，掩码投影求值；then 条件携带即拒）。"""

    # extra=forbid（T6b）：条件条目拒绝白名单外键（防呆共享单点
    # 与 condition_kernel 同语义——四消费者自动获得）
    model_config = ConfigDict(extra="forbid")

    subject: Union[str, IndicatorRef]
    action: str
    target: Union[float, str]
    timeframe: Optional[str] = None


class HypothesisConditions(BaseModel):
    """WHEN/THEN 结构化条件（机器判定唯一来源；陈述仅展示）。"""

    when_conditions: List[ConditionItem]
    then_conditions: List[ConditionItem]


class DataRangeItem(BaseModel):
    """半开区间 [start, end)，UTC ISO8601。

    start/end 可解析性前置校验（CARD-05）：非法串在此 422 拒绝——
    落到装载层后 pd.Timestamp 的回退链对非法串会抛 TypeError 裸穿
    500（存量洞）；校验与消费端同用 pd.Timestamp，杜绝「校验通过而
    消费端解析失败」。naive 形制合法（消费端按 UTC 解释——capability
    公开承诺）。
    """

    symbol: str
    timeframe: str
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _require_parseable_timestamp(cls, v: str) -> str:
        """时间串可解析性校验：解析失败 422（错误码+合法形 hint）。"""
        import pandas as pd

        try:
            pd.Timestamp(v)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "RESEARCH_PARAM_INVALID: 时间参数须为可解析的 UTC "
                "ISO8601 串（如 2024-01-01 或 2024-01-01T00:00:00Z），"
                f"got {v!r}") from exc
        return v


class AnalysisProvenance(BaseModel):
    """分析谱系引用（complete-research-tooling GAP-10 泛化）：声明假设
    来源的分析任务与候选指纹。

    两类引用（ref_type）：
    - screen_candidate：全谱扫描候选（旧形制=task_id+candidate_fingerprint
      两键必填——旧调用方零适配，model_validator 补默认值）；
    - analysis_task：网格/统计类分析任务来源（无指纹可引，指纹可选
      ——GAP-10 主场景）。

    两类同核（服务层）：task_id 存在性+任务记录区间事实核验保留
    （防虚构来源——声明不供数，区间一律取自任务记录）；
    不参与假设幂等键（同条件同区间重复登记命中同一假设）。
    """

    ref_type: Literal["screen_candidate", "analysis_task"] = "screen_candidate"
    task_id: str
    candidate_fingerprint: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_two_keys_compat(cls, data: Any) -> Any:
        """旧两键形制兼容读（GAP-10）：无 ref_type 键补默认
        screen_candidate——旧记录/旧调用方零适配。"""
        if isinstance(data, dict) and "ref_type" not in data:
            return {**data, "ref_type": "screen_candidate"}
        return data

    @model_validator(mode="after")
    def _fingerprint_conditionality(self) -> "AnalysisProvenance":
        """指纹条件性：screen_candidate 指纹必填（候选定位语义）；
        analysis_task 指纹可选（任务级引用无指纹可引）。"""
        if (self.ref_type == "screen_candidate"
                and not self.candidate_fingerprint):
            raise ValueError(
                "RESEARCH_PARAM_INVALID: ref_type=screen_candidate 须携带 "
                "candidate_fingerprint（候选定位语义；analysis_task "
                "任务级引用方可省略）")
        return self


# 兼容别名（GAP-10 泛化改名防导入断裂——旧导入方零适配）
ScreenProvenance = AnalysisProvenance


class HypothesisCreate(BaseModel):
    conditions: HypothesisConditions
    statement: str
    data_ranges: List[DataRangeItem]
    provenance: Optional[ScreenProvenance] = None
    # 条件集整体级触发策略（trigger-policy 卡2）：弱形制对象键——
    # mode 枚举/bars 参数的权威校验在服务层 validate_policy（422 附
    # 合法枚举与参数要求，错误即文档；API 层只声明键存在与对象形）
    trigger_policy: Optional[Dict[str, Any]] = None
    # 策略层参数声明块（0.4.2 R1，strategy-param-declaration 规格）：
    # 弱形制对象键（沿 trigger_policy 先例）——键白名单/值域分叉的
    # 权威校验在服务层 3.6 步；显式空对象 {}=显式声明「无参数」（与
    # 键缺失语义不同，幂等四分支分流）；不进 conditions/不进 hash
    strategy_params: Optional[Dict[str, Any]] = None
    # 分批条件组三顶层键（0.4.2 R3/DC-13，scaled-position-actions 规格）：
    # 路由层非 None 时并入 conditions dict（「归一非 None 才写键」——
    # 缺省不写键保 conditions_hash 连续，trigger_policy 先例；不走
    # HypothesisConditions 嵌套扩展——避 pydantic None 键 hash 断裂陷阱）
    exit_partial_conditions: Optional[List[ConditionItem]] = None
    add_trigger_conditions: Optional[List[ConditionItem]] = None
    add_suppress_conditions: Optional[List[ConditionItem]] = None
    # 交易方向键（add-direction-channel A1）：弱形制字符串键——值域
    # {long, short} 的权威校验在服务层 3.7 步（422 错误即文档）；
    # 缺省 None 与显式 "long" 语义相同（同指纹同实体）；归一形
    # short 才写 conditions 顶层键（服务层写键进身份指纹）
    direction: Optional[str] = None


class HypothesisTransitionRequest(BaseModel):
    target_status: str  # supported | refuted | superseded | abandoned
    evidence_ref: str  # 验证 record_id / 接替假设 id / abandoned 时为放弃说明


class HypothesisResponse(BaseModel):
    hypothesis_id: str
    status: str = "active"
    duplicated: bool = False
    # 交易方向回显（add-direction-channel）：实体属性键（每个假设都有
    # 方向，存量即 long）——新建/幂等命中均携带，既有消费方零适配
    direction: str = "long"
    # 登记自动预检警告段（add-hypothesis-precheck D6）：预检成功时携带
    # 三态结论与判定锚数值（锚=分析段实际触发数，多区间取最小值）；
    # 纯增量可选键——既有消费方零适配；幂等命中/预检降级不携带。
    precheck_warnings: Optional[Dict[str, Any]] = None
    # 既有声明块摘要（0.4.2 R1）：仅幂等 duplicated 路径携带（纯增量，
    # 沿 precheck_warnings 先例）；NULL 声明以 {} 呈现——旧调用方据此
    # 可知既有假设的声明内容而无需二次查询。
    existing_strategy_params: Optional[Dict[str, Any]] = None
    # 阈值单位可疑警告（add-threshold-unit-warning C-3）：仅新建路径且
    # 清单非空时携带（纯增量可选键——警告不阻断登记，语义独立于
    # precheck_warnings 的数据预检三态）；幂等命中不携带
    threshold_unit_warnings: Optional[List[Dict[str, Any]]] = None


class HypothesisListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    page: int = 1
    page_size: int = 50
    next_cursor: Optional[str] = None  # keyset 续页游标（末条 created_at|id）


class IsolatedBacktestRequest(BaseModel):
    hypothesis_id: str
    strategy_id: str
    request_range: DataRangeItem
    threshold_profile: Optional[Dict[str, Any]] = None


class StagedValidationRequest(BaseModel):
    """分段验证（add-staged-validation；检查⑤的分题型成绩单）。

    平铺形制（B1——SandboxBacktestRequest 先例）；hypothesis_id 可缺省
    （adhoc 纯日志留痕，verdict 照报 record_id=None）；segmentation 与
    segments 双模互斥（auto 缺省）。
    """

    strategy_id: str
    hypothesis_id: Optional[str] = None
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    start: str
    end: str
    segmentation: str = "auto"
    segments: Optional[List[Dict[str, Any]]] = None


class SandboxBacktestRequest(BaseModel):
    """沙盒回测（improve-mcp-agent-loop M6）：快速试错、可调成本、
    不判门槛不入验证记录；挂假设时区间计入判定并集（防窥探）。
    经济参数值域前置校验：fee/slippage >= 0、init_cash >= 1
    （负成本/零本金在下单金额计算中产生无意义或发散结果）。"""

    strategy_id: str
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    start: str
    end: str
    fee: float = Field(default=0.001, ge=0)
    slippage: float = Field(default=0.0005, ge=0)
    init_cash: float = Field(default=100000.0, ge=1)
    hypothesis_id: Optional[str] = None


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


class InspectCasesRequest(BaseModel):
    """案例抽查入口（add-inspect-cases；独立通道，不走模板族）。

    selector 两形态互斥（恰含 condition 或 timestamps 之一），服务层
    深度校验（结构化 422）。
    """

    symbol: str
    timeframe: str
    selector: Dict[str, Any]  # {condition:{subject,action,target}} 或 {timestamps:[...]}
    limit: int = 3
    context_bars: int = 8
    start: Optional[str] = None
    end: Optional[str] = None
    indicator: Optional[str] = None


class QueryIndicatorRequest(BaseModel):
    """指标读数入口（add-query-indicator；独立通道，检查④）。

    params 嵌套 dict（各指标参数名不同）走 POST body——与 inspect_cases
    的 selector 同一传输契约裁决；服务层深度校验（结构化 422）。

    0.4.1 R1 批量形制：可选 groups 与单组专属字段（indicator/params/
    reductions/start/end）互斥（同传 422 指明互斥对；symbol/timeframe
    公共必填保留）。groups MUST 显式声明——本模型无 model_config，
    pydantic 默认 ignore 会静默丢键使批量请求降级单组语义；indicator
    随之转可选（单组形制缺 indicator 由服务层 422）。
    """

    symbol: str
    timeframe: str
    indicator: Optional[str] = None
    params: Optional[Dict[str, Any]] = None  # 缺省拼注册表 default（Q1 裁决）
    reductions: Optional[List[str]] = None  # 缺省 ["latest","quantiles"]
    start: Optional[str] = None
    end: Optional[str] = None
    # 批量形制（0.4.1）：[{indicator, params?, reductions?, start?, end?}]
    groups: Optional[List[Dict[str, Any]]] = None


class FeatureRequestCreate(BaseModel):
    """慢通道需求单：title 上限 200 字符；payload 序列化上限 64KB
    （慢通道只保证持久化与可查——超大结构直入 DB 前置拒绝）。"""

    title: str = Field(max_length=200)
    payload: Dict[str, Any] = Field(default={})

    @field_validator("payload")
    @classmethod
    def _payload_size_cap(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if len(json.dumps(v, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise ValueError("payload 序列化超 64KB 上限")
        return v


class FeatureRequestResponse(BaseModel):
    request_id: str
    title: str
    status: str
    created_at: str
