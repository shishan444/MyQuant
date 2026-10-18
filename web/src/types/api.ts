/**
 * 语义别名层——前端域语言 ↔ 后端 Pydantic 模型的命名映射。
 *
 * 形状唯一权威 = ./generated/api.ts（openapi-typescript 从后端 openapi.json 生成，
 * 禁止手改）。契约调整流程：改后端 → `npm run generate:types` → 本层随 tsc 暴露差异。
 *
 * 本层只做三类事，不含独立于生成源的形状定义：
 * 1. 域语言重命名（映射处以「← 后端模型名」标注）；
 * 2. 消费端语义收窄：后端 string 的枚举字段收窄为判别联合；可选性以消费端现状为准，
 *    与后端声明不一致处注释标注；
 * 3. dict 洞补形：后端 `list[dict]`/`dict` 无元素 schema 的字段（openapi 表达洞），
 *    按实际 JSON 形状收窄；后端补子模型后应改回纯别名。
 *
 * 例外保留手写（均注明来源）：WS 推送消息（G2 事件契约冻结时收编 zod）、
 * 后端未声明 response_model 的端点响应、纯前端本地类型。
 */
import type { components } from "./generated/api";

type S = components["schemas"];

// ---------------------------------------------------------------------------
// Evolution / Strategy core (进化与策略核心域)
// ---------------------------------------------------------------------------

/**
 * 信号基因条件 ← 后端 SignalGeneModel.condition（dict 洞补形）。
 * 阈值字段名=threshold：core/strategy/executor.py 条件求值真实消费
 * `condition["threshold"]`（G12 批实证修正——原手写 value 为无依据幻觉）。
 */
export interface SignalGeneCondition {
  type: "lt" | "gt" | "le" | "ge" | "cross_above" | "cross_below" | "price_above" | "price_below";
  threshold?: number;
  ref_indicator?: string;
  ref_field?: string;
}

/** 信号基因 ← 后端 SignalGeneModel-Output；condition 为 dict 洞补形 */
export type SignalGene = Omit<S["SignalGeneModel-Output"], "condition"> & {
  condition: SignalGeneCondition;
};

/** 逻辑基因 ← 后端 LogicGenesModel；枚举收窄；add/reduce 后端必填（default AND），前端保持可选 */
export type LogicGenes = Omit<
  S["LogicGenesModel"],
  "entry_logic" | "exit_logic" | "add_logic" | "reduce_logic"
> & {
  entry_logic: "AND" | "OR";
  exit_logic: "AND" | "OR";
  add_logic?: "AND" | "OR";
  reduce_logic?: "AND" | "OR";
};

/** 执行基因 ← 后端 ExecutionGenesModel（形状一致） */
export type ExecutionGenes = S["ExecutionGenesModel"];

/** 风险基因 ← 后端 RiskGenesModel；direction/sl_mode 枚举收窄；sl_mode/atr_period 后端 default 兜底（openapi 因 default 标必填，消费端保持可选） */
export type RiskGenes = Omit<S["RiskGenesModel"], "direction" | "sl_mode" | "atr_period"> & {
  direction: "long" | "short" | "mixed";
  sl_mode?: "pct" | "atr";
  atr_period?: number;
};

/** 多周期层 ← 后端 DNAModel.layers（dict 洞补形：后端无 TimeframeLayer 子模型） */
export interface TimeframeLayerModel {
  timeframe: string;
  signal_genes: SignalGene[];
  logic_genes: LogicGenes;
  role?: string; // "structure" | "zone" | "execution"
}

/**
 * 策略 DNA ← 后端 DNAModel-Output。
 * - signal_genes/logic_genes/risk_genes/execution_genes 后端声明可选，
 *   进化构造与响应实际恒有——消费端收窄断言；
 * - layers 为 dict 洞补形；cross_layer_logic 枚举收窄；
 * - confluence_threshold/proximity_mult 后端必填（default），前端保持可选。
 */
export type DNA = Omit<
  S["DNAModel-Output"],
  | "signal_genes"
  | "logic_genes"
  | "execution_genes"
  | "risk_genes"
  | "layers"
  | "cross_layer_logic"
  | "confluence_threshold"
  | "proximity_mult"
> & {
  signal_genes: SignalGene[];
  logic_genes: LogicGenes;
  execution_genes: ExecutionGenes;
  risk_genes: RiskGenes;
  layers?: TimeframeLayerModel[] | null;
  cross_layer_logic?: "AND" | "OR";
  confluence_threshold?: number;
  proximity_mult?: number;
};

/** 策略 ← 后端 StrategyResponse；dna/metrics 以消费端收窄类型替代生成源原始形状 */
export type Strategy = Omit<S["StrategyResponse"], "dna" | "metrics"> & {
  dna?: DNA | null;
  metrics?: StrategyMetrics | null;
};

/** 回测结果 ← 后端 BacktestResponse；equity_curve/signals/dimension_scores 为 dict 洞补形 */
export type BacktestResult = Omit<
  S["BacktestResponse"],
  "equity_curve" | "signals" | "dimension_scores"
> & {
  dimension_scores?: Record<string, number>;
  equity_curve?: Array<{ timestamp: string; value: number }> | null;
  signals?: TradeSignal[] | null;
};

/** 交易信号 ← 后端 BacktestResponse.signals 元素（dict 洞补形） */
export interface TradeSignal {
  type: "buy" | "sell";
  timestamp: string;
  price: number;
  confidence?: number;
  reason: string;
}

/** 任务状态 ← 后端 string（EvolutionTaskResponse.status），消费端判别联合收窄 */
export type EvolutionTaskStatus = "pending" | "running" | "paused" | "stopped" | "completed";

/**
 * 进化任务 ← 后端 EvolutionTaskResponse。
 * - status/mode/direction 枚举收窄；
 * - champion_metrics/champion_dimension_scores 为 dict 洞补形；
 * - n_workers/elite_ratio/min_annual_return 等为后端字段（后端容器裁决移除中）。
 */
export type EvolutionTask = Omit<
  S["EvolutionTaskResponse"],
  | "status"
  | "mode"
  | "direction"
  | "initial_dna"
  | "champion_dna"
  | "champion_metrics"
  | "champion_dimension_scores"
  | "champion_satisfaction"
  | "progress_json"
> & {
  status: EvolutionTaskStatus;
  mode?: "auto" | "seed";
  direction: "long" | "short" | "mixed";
  initial_dna?: DNA;
  champion_dna?: DNA;
  champion_metrics?: {
    annual_return: number;
    sharpe_ratio: number;
    max_drawdown: number;
    win_rate: number | null; // 零交易缺失语义
    calmar_ratio: number;
    total_trades: number;
  };
  champion_dimension_scores?: Record<string, number>;
  champion_satisfaction?: Record<string, unknown>;
  progress_json?: Record<string, unknown>;
};

/** 历史记录 ← 后端 EvolutionHistoryRecord；best_fitness 等后端含 null，前端收窄为可选 */
export type EvolutionHistoryRecord = Omit<
  S["EvolutionHistoryRecord"],
  "best_fitness" | "avg_fitness" | "top3_summary"
> & {
  best_fitness?: number;
  avg_fitness?: number;
  top3_summary?: string;
};

/** 达标要求 ← 后端 RequirementsConfigModel；objective 后端必填（default sharpe），前端可选 */
export type RequirementsConfig = Omit<S["RequirementsConfigModel"], "objective"> & {
  objective?: string;
};

/** 数据集 ← 后端 DatasetResponse；quality_status 枚举收窄 */
export type Dataset = Omit<S["DatasetResponse"], "quality_status"> & {
  quality_status: "complete" | "warning" | "error" | "unknown";
};

/** K 线数据 ← 后端 OhlcvResponse；data 为 dict 洞补形 */
export type OhlcvData = Omit<S["OhlcvResponse"], "data"> & {
  data: Array<{
    timestamp: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
};

/** 前端分页容器辅助（泛型工具，非后端形状：各 List 响应自带 items/total） */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page?: number;
  page_size?: number;
}

/** 策略列表 ← 后端 StrategyListResponse；items 收窄为域类型 */
export type StrategyListResponse = Omit<S["StrategyListResponse"], "items"> & {
  items: Strategy[];
};

/** 任务列表 ← 后端 EvolutionTaskListResponse；items 收窄为域类型；page/page_size 后端必填（default 1/20），消费端保持可选 */
export type EvolutionTaskListResponse = Omit<
  S["EvolutionTaskListResponse"],
  "items" | "page" | "page_size"
> & {
  items: EvolutionTask[];
  page?: number;
  page_size?: number;
};

/** 数据集列表 ← 后端 DatasetListResponse；items 收窄为域类型 */
export type DatasetListResponse = Omit<S["DatasetListResponse"], "items"> & {
  items: Dataset[];
};

/**
 * 进化历史响应：service 层视图（非后端契约直译）。
 * 后端真实形状 = EvolutionHistoryResponse { task_id, generations }（见生成源），
 * services/evolution.ts 已适配为 { records, total }；元素为后端契约。
 */
export interface EvolutionHistoryResponse {
  records: EvolutionHistoryRecord[];
  total: number;
}

/**
 * WS 推送消息已收编实时契约层（src/realtime/schemas.ts，zod 判别联合），
 * 本层不再承载 WS 类型。
 */

/**
 * 策略指标 ← 后端 StrategyMetrics（Response.metrics 声明的 6 字段）。
 * - 6 字段后端声明可空，实际由回测产出后整体挂载（无 metrics 时整个字段为 null）——
 *   消费端收窄为必填；
 * - 消费端补形：core/scoring/metrics.py 实际计算含 calmar_ratio 等更多字段
 *   （经 dict 洞通道到达，如 champion_metrics/回测详情）；后端补全 response_model
 *   后应改回纯别名。
 */
export type StrategyMetrics = Omit<
  S["StrategyMetrics"],
  "annual_return" | "sharpe_ratio" | "max_drawdown" | "win_rate" | "total_trades" | "profit_factor"
> & {
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
    win_rate: number | null; // 零交易缺失语义
  total_trades: number;
  profit_factor: number;
  calmar_ratio?: number;
  sortino_ratio?: number;
  max_consecutive_losses?: number;
  monthly_consistency?: number;
  r_squared?: number;
};

/** 发现策略 ← /api/evolution/strategies 端点（响应未声明 response_model，dict 洞），按实际 JSON 形状 */
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

/** 变异记录 ← WS 推送（非 openapi 契约，G2 收编） */
export interface MutationRecord {
  generation: number;
  operation: string;
  details: string;
}

/** 进化产出策略 ← /api/evolution/tasks/{id}/strategies 端点（响应未声明 schema），按实际 JSON 形状 */
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

/** 条件输入 ← 后端 ConditionInput；logic 枚举收窄 */
export type ConditionInput = Omit<S["ConditionInput"], "logic"> & {
  logic: "AND" | "OR";
};

/** 验证请求 ← 后端 ValidateRequest */
export type ValidateRequest = S["ValidateRequest"];

/** 图表指标配置（纯前端本地类型：localStorage 持久化形态，作为 query 参数发往后端） */
export interface ChartIndicatorConfig {
  ema_periods: number[];
  ema_colors: string[];
  boll: { enabled: boolean; period: number; std: number; color: string };
  rsi: { enabled: boolean; period: number; overbought: number; oversold: number };
  vol: { enabled: boolean; position: "overlay" | "separate" };
}

/** 触发记录 ← 后端 TriggerRecordResponse */
export type TriggerRecord = S["TriggerRecordResponse"];

/** 分布桶 ← 后端 ValidateResponse.distribution 元素（dict 洞补形） */
export interface DistributionBucket {
  range: [number, number];
  match_count: number;
  mismatch_count: number;
  total_count: number;
}

/** 验证响应 ← 后端 ValidateResponse；distribution/concentration/extremes 为 dict 洞补形 */
export type ValidateResponse = Omit<
  S["ValidateResponse"],
  "distribution" | "concentration" | "extremes"
> & {
  distribution: DistributionBucket[];
  concentration: Record<string, number[]>;
  extremes: Array<{ change_pct: number; time: string; is_match: boolean }>;
};

// ---------------------------------------------------------------------------
// Available data sources
// ---------------------------------------------------------------------------

/** 可用数据源 ← 后端 AvailableSource */
export type AvailableSource = S["AvailableSource"];

/** 可用数据源列表 ← 后端 AvailableSourcesResponse */
export type AvailableSourcesResponse = S["AvailableSourcesResponse"];

// ---------------------------------------------------------------------------
// Rule validation (Strategy Lab v2.4)
// ---------------------------------------------------------------------------

/** 规则条件 ← 后端 RuleConditionInput；logic 枚举收窄（前端含行首 IF） */
export type RuleCondition = Omit<S["RuleConditionInput"], "logic"> & {
  logic: "IF" | "AND" | "OR";
};

/** 规则验证请求 ← 后端 RuleValidateRequest */
export type RuleValidateRequest = S["RuleValidateRequest"];

/** 规则验证响应 ← 后端 RuleValidateResponse */
export type RuleValidateResponse = S["RuleValidateResponse"];

// ---------------------------------------------------------------------------
// Chart indicators (computed by backend)
// ---------------------------------------------------------------------------

/** 图表指标响应 ← /api/data/chart-indicators 端点（响应未声明 response_model），按实际 JSON 形状 */
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

/** 验证日期区间 ← 后端 VerifyDateRange */
export type VerifyDateRange = S["VerifyDateRange"];

/** 验证请求 ← 后端 VerifyRequest */
export type VerifyRequest = S["VerifyRequest"];

/** 验证明细 ← 后端 VerifyResultItem */
export type VerifyResultItem = S["VerifyResultItem"];

/** 周期汇总 ← 后端 VerifyPeriodSummary */
export type VerifyPeriodSummary = S["VerifyPeriodSummary"];

/** 策略汇总 ← 后端 VerifySummaryItem */
export type VerifySummaryItem = S["VerifySummaryItem"];

/** 验证响应 ← 后端 VerifyResponse */
export type VerifyResponse = S["VerifyResponse"];

/** 验证历史条目 ← 后端 VerifyHistoryItem */
export type VerifyHistoryItem = S["VerifyHistoryItem"];

/** 验证历史响应 ← 后端 VerifyHistoryResponse */
export type VerifyHistoryResponse = S["VerifyHistoryResponse"];

/** 验证会话 ← 后端 VerifySessionResponse */
export type VerifySession = S["VerifySessionResponse"];

/** 验证会话列表 ← 后端 VerifySessionListResponse */
export type VerifySessionListResponse = S["VerifySessionListResponse"];

// ---------------------------------------------------------------------------
// Batch Backtest (multi-strategy × multi-period detailed backtesting)
// ---------------------------------------------------------------------------

/** 批量回测请求 ← 后端 BatchBacktestRequest */
export type BatchBacktestRequest = S["BatchBacktestRequest"];

/** 批量回测结果明细 ← /api/strategies/batch-backtest/{id} 端点（响应未声明 response_model），按实际 JSON 形状 */
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
    win_rate: number | null; // 零交易缺失语义
  total_trades: number;
  profit_factor: number;
  fitness: number;
  qualified: boolean;
  liquidated: boolean;
  total_funding_cost: number;
  error?: string;
}

/** 批量回测策略汇总 ← 同上端点（响应未声明 schema），按实际 JSON 形状 */
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

// ---------------------------------------------------------------------------
// Trading (纸盘交易域——/api/paper-trading 新域，rebuild-paper-trading 切换)
// ---------------------------------------------------------------------------

/** 任务状态 ← 后端 PaperTaskResponse.status（string），消费端判别联合收窄（五态） */
export type PaperTaskStatus = "pending" | "running" | "completed" | "stopped" | "error";

/**
 * 纸盘任务 ← 后端 PaperTaskResponse；status 收窄五态联合。
 * 账户运行态组（balance/position_* 等）后端声明可选，未运行恒 null——
 * 消费端按可空透传（P0 权益修复：NULL 不折算 0 假值）。
 */
export type TradingTask = Omit<S["PaperTaskResponse"], "status"> & {
  status: PaperTaskStatus;
};

/** 任务列表 ← 后端 PaperTaskListResponse；tasks 收窄为域类型 */
export type TradingTaskList = Omit<S["PaperTaskListResponse"], "tasks"> & {
  tasks: TradingTask[];
};

/** 纸盘成交 ← 后端 PaperFillResponse（形状一致；side/price 等可空字段透传） */
export type PaperFill = S["PaperFillResponse"];

/** 成交流水 ← 后端 PaperFillListResponse（fills+limit+offset+has_more 探测式分页） */
export type PaperFillList = S["PaperFillListResponse"];

/**
 * 建任务参数 ← 后端 PaperTaskCreate；default 必填字段构造端保持可选
 * （strategy_id 必给——上盘流转只收策略编号，DNA 由后端从策略库快照；
 * 语义参数 direction/timeframe 无参数位，传入即 422）。
 */
export type CreateTradingTaskParams = Partial<S["PaperTaskCreate"]> & {
  strategy_id: string;
};

/** 权益快照 ← 后端 PaperEquityResponse（形状一致） */
export type EquitySnapshot = S["PaperEquityResponse"];

/** 权益曲线 ← 后端 PaperEquityListResponse（snapshots+limit+offset+has_more） */
export type EquitySnapshotList = S["PaperEquityListResponse"];

/** 资金费记录 ← 后端 PaperFundingResponse（三态可见对账披露面） */
export type FundingRecord = S["PaperFundingResponse"];

/** 资金费列表 ← 后端 PaperFundingListResponse */
export type FundingRecordList = S["PaperFundingListResponse"];

/**
 * 滚动报告 ← GET /api/paper-trading/tasks/{id}/report 端点（响应为裸 dict
 * 无 schema），按 core/paper_trading/reporting.py 三层结构补形（任意状态
 * 可取；报告结构无自动判定字段——考场无观点）。
 */
export interface TradingReport {
  task_id: string;
  strategy_id: string;
  status: string;
  generated_at: string;
  performance: TradingReportPerformance;
  deviation: TradingReportDeviation;
  evidence: TradingReportEvidence;
}

/**
 * 绝对绩效层 ← compute_metrics 单源输出（core/scoring/metrics.py 回测族）。
 * 核心键必填；扩展键（benchmark 族等）视输入存在，消费端可选。
 */
export interface TradingReportPerformance {
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number; // 负小数（drawdown.min()）
  win_rate: number | null; // 零交易缺失语义
  calmar_ratio: number;
  total_trades: number;
  sortino_ratio: number;
  profit_factor: number;
  max_consecutive_losses: number;
  monthly_consistency: number;
  r_squared: number;
  total_bars?: number;
  market_annual_return?: number;
  alpha?: number;
  backtest_years?: number;
  benchmark_return?: number | null;
  excess_return?: number | null;
}

/**
 * 偏差对照层 ← 同策略同参数任务区间回测预期成绩+diff+归因披露。
 * 对照失败该层标 unavailable（backtest=null），整份报告不失败。
 */
export interface TradingReportDeviation {
  backtest: Record<string, unknown> | null;
  diff: {
    total_return: number | null;
    sharpe_ratio: number | null;
    max_drawdown: number | null;
    win_rate: number | null;
  };
  unavailable_reason: string | null;
  attribution_notes: string[];
}

/** 证据强度层 ← 60/5 双阈值+研究状态快照+funding 三态两计数 */
export interface TradingReportEvidence {
  bars_run: number;
  closed_trades: number;
  position_bars: number;
  insufficient_evidence: boolean;
  research_status: string;
  funding_settled_points: number;
  funding_pending_points: number | null; // 无 funding 行=None（区别于 0 笔挂起）
  funding_post_terminal_backfill: { points: number; total_amount: number } | null;
}

/** 结题报告外壳 ← 后端 FinalReportResponse；report dict 洞收窄为报告体 */
export type TradingFinalReport = Omit<S["FinalReportResponse"], "report"> & {
  report: TradingReport;
};

/** runner 状态 ← /api/paper-trading/runner-status 端点（响应为裸 dict 无 schema），按 core/paper_trading/runtime/scheduler.py status() */
export interface TradingRunnerStatus {
  max_running_tasks: number | null;
  running_count: number;
  active_task_ids: string[];
  pending_depth: number;
}
