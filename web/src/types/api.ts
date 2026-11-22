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
  sl_mode?: "pct" | "atr" | "trail" | "series";
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

/** 数据集列表 ← 后端 DatasetListResponse；items 收窄为域类型 */
export type DatasetListResponse = Omit<S["DatasetListResponse"], "items"> & {
  items: Dataset[];
};

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

// ---------------------------------------------------------------------------
// Validation types (Strategy Lab v2.3)
// ---------------------------------------------------------------------------

/**
 * 条件输入（形状自退役前生成物内联——fix-replay-gap-alignment U10：
 * 后端 ConditionInput schema 已随 retire-legacy 移出 openapi，本类型为
 * dna-generator 活消费保留）；0.2.13 混合形制：参数化指标结构化
 * {indicator, params, field} / 基础列字符串精确名。
 */
export interface ConditionInput {
  /** Subject（混合形制：结构化指标引用或列名字符串） */
  subject: string | { indicator: string; params: Record<string, unknown>; field?: string | null };
  /** Action（比较/穿越动作名） */
  action: string;
  /** Target（阈值或对端引用，前端形制恒字符串） */
  target: string;
  /** Window（时序动作窗口） */
  window?: number | null;
  /** Logic（枚举收窄——生成源 string） */
  logic: "AND" | "OR";
  /** Timeframe（高周期条件键） */
  timeframe?: string | null;
}

/** 图表指标配置（纯前端本地类型：localStorage 持久化形态，作为 query 参数发往后端） */
export interface ChartIndicatorConfig {
  ema_periods: number[];
  ema_colors: string[];
  boll: { enabled: boolean; period: number; std: number; color: string };
  rsi: { enabled: boolean; period: number; overbought: number; oversold: number };
  vol: { enabled: boolean; position: "overlay" | "separate" };
}

// （ValidateRequest/TriggerRecord/ValidateResponse 族随 services/validation
//  退役零消费删除——fix-replay-gap-alignment U8；ConditionInput 为
//  dna-generator 活消费保留）

// ---------------------------------------------------------------------------
// 可用数据源
// ---------------------------------------------------------------------------

/** 可用数据源 ← 后端 AvailableSource */
export type AvailableSource = S["AvailableSource"];

/** 可用数据源列表 ← 后端 AvailableSourcesResponse */
export type AvailableSourcesResponse = S["AvailableSourcesResponse"];

// ---------------------------------------------------------------------------
// Rule validation (Strategy Lab v2.4)
// ---------------------------------------------------------------------------

/**
 * 规则条件（形状自退役前生成物内联——fix-replay-gap-alignment U10：
 * 后端 RuleConditionInput schema 已随 retire-legacy 移出 openapi，本类型
 * 为 Lab 条件构建器活消费保留）；logic 枚举收窄（前端含行首 IF）。
 */
export interface RuleCondition {
  logic: "IF" | "AND" | "OR";
  timeframe: string;
  subject: string;
  action: string;
  target: string;
}

// （RuleValidateRequest/RuleValidateResponse 随 services/validation 退役
//  零消费删除——fix-replay-gap-alignment U8；RuleCondition 为 Lab 条件
//  构建器活消费保留）

// ---------------------------------------------------------------------------
// 图表指标（后端计算）
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

// （Strategy Verify 域十类型〔DateRange/Request/ResultItem/PeriodSummary/
//  SummaryItem/Response/HistoryItem/HistoryResponse/Session/SessionList〕
//  与 VS 冻结 import 随 verify 会话体系退役整段删除——
//  fix-replay-gap-alignment U8；generated-verify-frozen.ts 同批删）

// ---------------------------------------------------------------------------
// Trading (模拟交易域——/api/paper-trading 新域，rebuild-paper-trading 切换)
// ---------------------------------------------------------------------------

/** 任务状态 ← 后端 PaperTaskResponse.status（string），消费端判别联合收窄（五态） */
export type PaperTaskStatus = "pending" | "running" | "completed" | "stopped" | "error";

/**
 * 模拟任务 ← 后端 PaperTaskResponse；status 收窄五态联合。
 * 账户运行态组（balance/position_* 等）后端声明可选，未运行恒 null——
 * 消费端按可空透传（P0 权益修复：NULL 不折算 0 假值）。
 */
export type TradingTask = Omit<S["PaperTaskResponse"], "status"> & {
  status: PaperTaskStatus;
  /** 达标两维（查询期现算——后端缺省 null 场景兜底） */
  qualification_progress?: {
    pnl_positive: boolean;
    trades_enough: boolean;
    first_met_at: string | null;
  } | null;
};

/** 任务列表 ← 后端 PaperTaskListResponse；tasks 收窄为域类型 */
export type TradingTaskList = Omit<S["PaperTaskListResponse"], "tasks"> & {
  tasks: TradingTask[];
};

/** 模拟成交 ← 后端 PaperFillResponse（形状一致；side/price 等可空字段透传） */
export type PaperFill = S["PaperFillResponse"];
export type PaperSignal = S["PaperSignalResponse"];
export type PaperSignalList = S["PaperSignalListResponse"];

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
  /** 达标判定段（paper-trading-revamp：三维终判——旧报告无此键合法，可选） */
  qualification?: TradingReportQualification;
}

/** qualification 段（三维终判：两维+触发一致性三态+凭证迁移） */
export interface TradingReportQualification {
  judged_at: string | null;
  pnl_positive: boolean;
  trades_enough: boolean;
  trigger_consistency: {
    passed: boolean | null;
    deviation_ratio: number | null;
    numerator: number | null;
    denominator: number;
    detail?: Record<string, unknown>;
  };
  qualified: boolean;
  profile_note?: string;
  transition?: {
    attempted: boolean;
    migrated: boolean;
    not_migrated_reason: string | null;
  };
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
 * 偏差对照层 ← 同策略同参数任务区间对照预期成绩+diff+归因披露。
 * 对照失败该层标 unavailable（backtest=null），整份报告不失败。
 * 对照源两形（fix-paper-deviation-contrast）：final=replay（回放引擎
 * 同构直跑，diff=纯执行环境差）；rolling=legacy_vbt（缺陷差计入 diff，
 * 注记第五项披露）；回放任务判域排除（不适用对照）为 null；旧报告
 * 无此键合法（向后兼容纯增键）。
 */
export interface TradingReportDeviation {
  backtest: Record<string, unknown> | null;
  diff: {
    total_return: number | null;
    sharpe_ratio: number | null;
    max_drawdown: number | null;
    win_rate: number | null;
  };
  contrast_source?: "replay" | "legacy_vbt" | null;
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
