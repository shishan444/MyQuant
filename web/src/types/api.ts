export interface SignalGene {
  indicator: string;
  params: Record<string, unknown>;
  role: "entry_trigger" | "entry_guard" | "exit_trigger" | "exit_guard" | "add_trigger" | "add_guard" | "reduce_trigger" | "reduce_guard" | "direction";
  field?: string;
  condition: {
    type: "lt" | "gt" | "le" | "ge" | "cross_above" | "cross_below" | "price_above" | "price_below";
    value?: number;
    ref_indicator?: string;
    ref_field?: string;
  };
}

export interface LogicGenes {
  entry_logic: "AND" | "OR";
  exit_logic: "AND" | "OR";
  add_logic?: "AND" | "OR";
  reduce_logic?: "AND" | "OR";
}

export interface ExecutionGenes {
  timeframe: string;
  symbol: string;
}

export interface RiskGenes {
  stop_loss: number;
  take_profit: number | null;
  position_size: number;
  leverage: number;
  direction: "long" | "short" | "mixed";
  sl_mode?: "pct" | "atr";
  atr_period?: number;
}

export interface TimeframeLayerModel {
  timeframe: string;
  signal_genes: SignalGene[];
  logic_genes: LogicGenes;
  role?: string; // "structure" | "zone" | "execution"
}

export interface DNA {
  signal_genes: SignalGene[];
  logic_genes: LogicGenes;
  execution_genes: ExecutionGenes;
  risk_genes: RiskGenes;
  strategy_id?: string;
  generation: number;
  parent_ids: string[];
  mutation_ops: string[];
  layers?: TimeframeLayerModel[] | null;
  cross_layer_logic?: "AND" | "OR";
  mtf_mode?: string | null;
  confluence_threshold?: number;
  proximity_mult?: number;
}

export interface Strategy {
  strategy_id: string;
  name?: string;
  dna?: DNA;
  symbol: string;
  timeframe: string;
  source: string;
  source_task_id?: string;
  best_score?: number;
  best_fitness?: number;
  qualified?: boolean | null;
  metrics?: StrategyMetrics | null;
  generation: number;
  parent_ids?: string;
  tags?: string;
  notes?: string;
  verify_count?: number;
  verify_avg_score?: number | null;
  verify_best_score?: number | null;
  last_verified_at?: string | null;
  verify_star?: number | null;
  created_at: string;
  updated_at: string;
}

export interface BacktestResult {
  result_id: string;
  strategy_id: string;
  symbol: string;
  timeframe: string;
  data_start?: string;
  data_end?: string;
  init_cash: number;
  fee: number;
  slippage: number;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  total_score: number;
  fitness: number;
  qualified: boolean;
  template_name: string;
  dimension_scores?: Record<string, number>;
  satisfaction?: Record<string, unknown>;
  run_source: string;
  equity_curve?: Array<{ timestamp: string; value: number }>;
  signals?: TradeSignal[];
  total_funding_cost: number;
  liquidated: boolean;
}

export interface TradeSignal {
  type: "buy" | "sell";
  timestamp: string;
  price: number;
  confidence?: number;
  reason: string;
}

export type EvolutionTaskStatus = "pending" | "running" | "paused" | "stopped" | "completed";

export interface EvolutionTask {
  task_id: string;
  status: EvolutionTaskStatus;
  target_score: number;
  score_template: string;
  symbol: string;
  timeframe: string;
  initial_dna?: DNA;
  champion_dna?: DNA;
  population_size: number;
  max_generations: number;
  current_generation: number;
  created_at: string;
  updated_at: string;
  stop_reason?: string;
  best_score?: number;
  best_fitness?: number;
  requirements?: RequirementsConfig;
  qualified_count?: number;
  indicator_pool?: string[];
  timeframe_pool?: string[];
  mode?: "auto" | "seed";
  leverage: number;
  direction: "long" | "short" | "mixed";
  data_start?: string;
  data_end?: string;
  data_time_start?: string;
  data_time_end?: string;
  data_row_count?: number;
  champion_metrics?: {
    annual_return: number;
    sharpe_ratio: number;
    max_drawdown: number;
    win_rate: number;
    calmar_ratio: number;
    total_trades: number;
  };
  champion_dimension_scores?: Record<string, number>;
  champion_satisfaction?: Record<string, unknown>;
  continuous?: boolean;
  population_count?: number;
  strategy_threshold?: number;
  strategy_count?: number;
  exploration_efficiency?: number;
  current_phase?: string;
  progress_json?: Record<string, unknown>;
}

export interface EvolutionHistoryRecord {
  generation: number;
  best_score: number;
  avg_score: number;
  best_fitness?: number;
  avg_fitness?: number;
  top3_summary?: string;
  created_at: string;
}

export interface RequirementsConfig {
  objective?: string;
  min_annual_return: number;
  max_drawdown: number;
  min_win_rate: number;
  min_total_trades: number;
  min_profit_factor: number;
}

export interface Dataset {
  dataset_id: string;
  symbol: string;
  interval: string;
  row_count: number;
  time_start?: string;
  time_end?: string;
  quality_status: "complete" | "warning" | "error" | "unknown";
  gap_count: number;
  created_at: string;
  updated_at: string;
}

export interface OhlcvData {
  dataset_id: string;
  data: Array<{
    timestamp: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page?: number;
  page_size?: number;
}

export interface StrategyListResponse {
  items: Strategy[];
  total: number;
}

export interface EvolutionTaskListResponse {
  items: EvolutionTask[];
  total: number;
  page?: number;
  page_size?: number;
}

export interface DatasetListResponse {
  items: Dataset[];
  total: number;
}

export interface EvolutionHistoryResponse {
  records: EvolutionHistoryRecord[];
  total: number;
}

export interface GenerationUpdate {
  // F0 最小扩充：补齐 runner 实际推送的消息类型与字段（useEvolution.ts 已在消费）；
  // F2 WS 层统一时将收窄为判别联合并经 zod 校验
  type:
    | "generation_complete"
    | "evolution_complete"
    | "population_started"
    | "strategy_discovered"
    | "task_started"
    | "task_snapshot"
    | "phase_changed";
  task_id: string;
  generation: number;
  best_score: number;
  avg_score: number;
  target_score: number;
  max_generations: number;
  champion_dna?: DNA;
  population_diversity?: number;
  last_mutations?: string[];
  population_count?: number;
  best_score_ever?: number;
  best_fitness?: number;
  qualified_count?: number;
  fitness?: number;
  qualified?: boolean;
  total_generations_so_far?: number;
  strategy_id?: string;
  score?: number;
  name?: string;
  current_generation?: number;
  current_phase?: string;
  status?: string;
  phase?: string;
}

export interface StrategyMetrics {
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  calmar_ratio: number;
  profit_factor: number;
  sortino_ratio?: number;
  max_consecutive_losses?: number;
  monthly_consistency?: number;
  r_squared?: number;
}

export interface DiscoveredStrategy {
  strategy_id: string;
  name: string;
  dna: DNA;
  source: "evolution";
  source_task_id: string;
  score: number;
  generation: number;
  created_at: string;
  symbol: string;
  timeframe: string;
  metrics?: StrategyMetrics | null;
  fitness?: number;
  qualified?: boolean;
}

export interface MutationRecord {
  generation: number;
  operation: string;
  details: string;
}

export interface EvolvedStrategy {
  strategy_id: string;
  dna: DNA;
  source: "champion" | "snapshot";
  generation?: number;
  score: number;
  fitness?: number;
  qualified?: boolean;
}

// ---------------------------------------------------------------------------
// Validation types (Strategy Lab v2.3)
// ---------------------------------------------------------------------------

export interface ConditionInput {
  subject: string;
  action: string;
  target: string;
  window?: number;
  logic: "AND" | "OR";
  timeframe?: string;
}

export interface ValidateRequest {
  pair: string;
  timeframe: string;
  base_timeframe?: string;
  start: string;
  end: string;
  when: ConditionInput[];
  then: ConditionInput[];
  indicator_params?: Record<string, unknown>;
}

export interface ChartIndicatorConfig {
  ema_periods: number[];
  ema_colors: string[];
  boll: { enabled: boolean; period: number; std: number; color: string };
  rsi: { enabled: boolean; period: number; overbought: number; oversold: number };
  vol: { enabled: boolean; position: "overlay" | "separate" };
}

export interface TriggerRecord {
  id: number;
  time: string;
  trigger_price: number;
  change_pct: number;
  matched: boolean;
  indicator_values: Record<string, number>;
}

export interface DistributionBucket {
  range: [number, number];
  match_count: number;
  mismatch_count: number;
  total_count: number;
}

export interface ValidateResponse {
  match_rate: number;
  total_count: number;
  match_count: number;
  mismatch_count: number;
  triggers: TriggerRecord[];
  distribution: DistributionBucket[];
  percentiles: Record<string, number>;
  concentration: Record<string, number[]>;
  signal_frequency: Record<string, number>;
  extremes: Array<{ change_pct: number; time: string; is_match: boolean }>;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Available data sources
// ---------------------------------------------------------------------------

export interface AvailableSource {
  symbol: string;
  timeframe: string;
  time_start?: string;
  time_end?: string;
}

export interface AvailableSourcesResponse {
  sources: AvailableSource[];
}

// ---------------------------------------------------------------------------
// Rule validation (Strategy Lab v2.4)
// ---------------------------------------------------------------------------

export interface RuleCondition {
  logic: "IF" | "AND" | "OR";
  timeframe: string;
  subject: string;
  action: string;
  target: string;
}

export interface RuleValidateRequest {
  pair: string;
  timeframe: string;
  start: string;
  end: string;
  entry_conditions: RuleCondition[];
  exit_conditions: RuleCondition[];
}

export interface RuleValidateResponse {
  buy_signals: Array<{ time: string; price: number; type: string }>;
  sell_signals: Array<{ time: string; price: number; type: string }>;
  trades: Array<{
    entry_time: string;
    entry_price: number;
    exit_time: string;
    exit_price: number;
    return_pct: number;
    is_win: boolean;
  }>;
  total_trades: number;
  win_trades: number;
  loss_trades: number;
  win_rate: number;
  total_return_pct: number;
  avg_return_pct: number;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Chart indicators (computed by backend)
// ---------------------------------------------------------------------------

export interface ChartIndicatorsResponse {
  ema: Record<string, Array<{ time: string; value: number }>>;
  boll: {
    upper: Array<{ time: string; value: number }>;
    middle: Array<{ time: string; value: number }>;
    lower: Array<{ time: string; value: number }>;
  } | null;
  rsi: Array<{ time: string; value: number }> | null;
  macd: {
    macd: Array<{ time: string; value: number }>;
    signal: Array<{ time: string; value: number }>;
    histogram: Array<{ time: string; value: number }>;
  } | null;
  kdj: {
    k: Array<{ time: string; value: number }>;
    d: Array<{ time: string; value: number }>;
    j: Array<{ time: string; value: number }>;
  } | null;
}

// ---------------------------------------------------------------------------
// Strategy Verify (multi-period validation)
// ---------------------------------------------------------------------------

export interface VerifyDateRange {
  start: string;
  end: string;
}

export interface VerifyRequest {
  strategy_ids: string[];
  data_ranges: VerifyDateRange[];
  init_cash?: number;
  fee?: number;
  slippage?: number;
}

export interface VerifyResultItem {
  strategy_id: string;
  data_start: string;
  data_end: string;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
  fitness: number;
  qualified: boolean;
  error?: string;
}

export interface VerifyPeriodSummary {
  data_start: string;
  data_end: string;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  fitness: number;
  qualified: boolean;
}

export interface VerifySummaryItem {
  strategy_id: string;
  strategy_name: string;
  comprehensive_score: number;
  avg_fitness: number;
  qualified_count: number;
  total_periods: number;
  per_period_metrics: VerifyPeriodSummary[];
}

export interface VerifyResponse {
  results: VerifyResultItem[];
  summary: VerifySummaryItem[];
}

export interface VerifyHistoryItem {
  result_id: string;
  strategy_id: string;
  strategy_name?: string;
  symbol: string;
  timeframe: string;
  data_start: string;
  data_end: string;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  fitness: number;
  qualified: number;
  created_at: string;
}

export interface VerifyHistoryResponse {
  items: VerifyHistoryItem[];
  total: number;
}

export interface VerifySession {
  session_id: string;
  status: string;
  strategy_ids: string;
  data_ranges: string;
  init_cash: number;
  fee: number;
  slippage: number;
  summary_json?: string;
  total_results: number;
  total_strategies: number;
  error_message?: string;
  created_at: string;
  completed_at?: string;
}

export interface VerifySessionListResponse {
  items: VerifySession[];
  total: number;
}

// ---------------------------------------------------------------------------
// Batch Backtest (multi-strategy × multi-period detailed backtesting)
// ---------------------------------------------------------------------------

export interface BatchBacktestRequest {
  strategy_ids: string[];
  data_ranges: Array<{ start: string; end: string }>;
  init_cash?: number;
  fee?: number;
  slippage?: number;
  leverage?: number;
}

export interface BatchBacktestResultItem {
  result_id: string;
  strategy_id: string;
  strategy_name: string;
  symbol: string;
  timeframe: string;
  data_start: string;
  data_end: string;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
  fitness: number;
  qualified: boolean;
  liquidated: boolean;
  total_funding_cost: number;
  error?: string;
}

export interface BatchBacktestSummaryItem {
  strategy_id: string;
  strategy_name: string;
  symbol: string;
  timeframe: string;
  avg_total_return: number;
  avg_sharpe_ratio: number;
  worst_max_drawdown: number;
  avg_fitness: number;
  qualified_count: number;
  total_periods: number;
  per_period_results: BatchBacktestResultItem[];
}
