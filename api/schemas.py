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
                      field_validator)

# 值域单源引用（D4 家族/0.4.2 值域单源化）：sl_mode 取值域与杠杆红线
# 的权威常量在 core 层，传输层只派生消费——改值单点生效，禁字面重复
from core.strategy.dna import SL_MODES
from core.strategy.validator import MAX_LEVERAGE

# sl_mode pattern 由 SL_MODES 派生（封闭枚举单源——取值域扩展时
# 此处自动跟随，无需改传输层）
_SL_MODE_PATTERN = "^(" + "|".join(SL_MODES) + ")$"


# ── 枚举 ──


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


# ── DNA 组件模型 ──


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
    # 动态定仓（complete-research-tooling 任务 4 同步铁律）：枚举/值域
    # 权威在 validator（传输层不加 bound/pattern——五键先例同款）
    position_mode: Optional[str] = None
    risk_fraction: Optional[float] = None
    # 仓位参数（add-position-sizing-params 同步铁律）：首笔比例 (0,1]
    # 与金额防线 >0——值域权威在 validator（传输层不加 bound，先例同款）
    initial_fraction: Optional[float] = None
    max_position_loss: Optional[float] = None
    # series 档止损线来源声明（dynamic-stop-loss-series 同步铁律）：
    # 两形制互斥 {indicator, params, field?}（field 单输出指标可省）/
    # {column: fx_ 前缀精确列名}——结构/互斥/前缀/值域权威在
    # validator（传输层不加 bound，先例同款）；漏键会使 extra="forbid"
    # 拒收 series 档 DNA 直传，且 crud 读回 ValidationError 被吞 →
    # GET 响应 dna=null（DNA 静默消失）
    sl_series_source: Optional[dict] = None


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
    # H4-c 强形制（dna-strong-typing-and-disclosure-hygiene 件④）：
    # List[dict] → List[TimeframeLayerModel]——坏形（条目非 dict/缺
    # timeframe/signal_genes 元素坏形/多余键）传输层 422（pydantic
    # 既有通道），先于 to_dna() 与持久化，不再以未包装 TypeError 落
    # 500。timeframe/role 值域不在此判（装载侧 TIMEFRAME_UNKNOWN 族与
    # validator 层判据白名单为既有权威，防双头）；存量四键层与模型
    # 字段一一对应，读库零迁移
    layers: Optional[List["TimeframeLayerModel"]] = None
    cross_layer_logic: str = "AND"
    mtf_mode: Optional[str] = None
    confluence_threshold: float = 0.3
    proximity_mult: float = 1.5
    # == v2 超集（add-ai-research-mcp 6.1）：承载 signal_sources 与 composition ==
    signal_sources: Optional[List[Dict[str, Any]]] = None
    composition: Optional[Dict[str, Any]] = None
    # 入场触发策略（trigger-policy 卡5 / D11）：四消费面（evolution
    # initial/champion、策略创建读回、compare、batch）传输键
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


# ── 策略域模型 ──


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
    # 【冻结快照·retire-verify-sessions B5（design Q1 选项 A）】verify
    # 组五列：统计累计写入者已随五端点删除（请求 404），历史值只读留档；
    # 唯一写点=revalidate 归档重启的作废重置（置 0/NULL，重置前快照进
    # 迁移事件 evidence）。读出反映旧验证会话体系时代的存量数据。
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


# ── 配置域模型 ──


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
    # R4 时序动作族可选键（fix-mtf-execution-consistency）：window=开窗
    # 根数（>=1 整数，值域校验在翻译/分析链）；inner_action=内层动作
    # （合法集 gt/lt/ge/le/price_above——与分析链 _LOOKBACK_INNER_
    # ACTIONS 同源）；内层阈值由 target 兼任（沿 lookback 族先例）
    window: Optional[int] = None
    inner_action: Optional[str] = None
    # R1 支撑/阻力动作族可选键（indicator-and-action-repair 卡 E；
    # review2 CARD-03 补 schema 同步——R4 先例同款遗漏收口）：
    # touch_bounce 携带 direction（支撑/阻力侧）与 proximity_pct
    # （近线距离比例）；role_reversal 携带 role 与 lookback（回看
    # 根数）——不写键即用求值层缺省（direction=support/
    # proximity_pct=0.01/role=resistance/lookback=10，翻译产物只含
    # 登记携带键）；值域权威校验在服务层登记链（B6 段）
    direction: Optional[str] = None
    proximity_pct: Optional[float] = None
    role: Optional[str] = None
    lookback: Optional[int] = None


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


class SandboxBacktestRequest(BaseModel):
    """沙盒回测（improve-mcp-agent-loop M6）：快速试错、可调成本、
    不判门槛不构成验证证据；每次执行入验证记录统一流（level=
    sandbox_backtest 行，响应回显 record_id）；挂假设时区间计入
    判定并集（防窥探）。
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
    # 任务级封顶两参（sandbox-and-report-supplement D1 ③层）：宽松
    # float 类型，值域（leverage 整数 [1,3]／margin_cap (0,0.60]）在
    # 路由层 require_task_cap_params 单点收窄（结构化 422 附
    # legal_range）；None=不生效（存量逐位不变）
    leverage: Optional[float] = None
    margin_cap: Optional[float] = None
    # 账户级硬熔断（D3①——沙盒路）：权益自峰值回撤比率阈值 (0,1)，
    # None=不启用；值域校验在路由层（422 附 legal_range）
    halt_drawdown_pct: Optional[float] = None


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


