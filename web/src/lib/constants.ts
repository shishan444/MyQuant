/**
 * Shared constants used across evolution, lab, and strategy modules.
 */

// ---------------------------------------------------------------------------
// Symbols
// ---------------------------------------------------------------------------

export const SYMBOL_OPTIONS = [
  { value: "BTCUSDT", label: "BTC/USDT" },
  { value: "ETHUSDT", label: "ETH/USDT" },
  { value: "BNBUSDT", label: "BNB/USDT" },
  { value: "SOLUSDT", label: "SOL/USDT" },
] as const;

// ---------------------------------------------------------------------------
// Timeframes
// ---------------------------------------------------------------------------

export const TIMEFRAME_POOL_OPTIONS = ["1d", "4h", "1h", "30m", "15m", "5m", "1m", "3d"] as const;

export const TIMEFRAME_LABELS: Record<string, string> = {
  "1d": "1D",
  "4h": "4H",
  "1h": "1H",
  "30m": "30M",
  "15m": "15M",
  "5m": "5M",
  "1m": "1M",
  "3d": "3D",
};

// 下拉全量选项（中文标签、更全集）
export const TIMEFRAME_SELECT_OPTIONS = [
  { value: "15m", label: "15分钟" },
  { value: "1h", label: "1小时" },
  { value: "4h", label: "4小时" },
  { value: "1d", label: "1天" },
  { value: "1w", label: "1周" },
] as const;

// 演化表单用短标签
export const TIMEFRAME_FORM_OPTIONS = [
  { value: "1d", label: "1D" },
  { value: "3d", label: "3D" },
  { value: "4h", label: "4H" },
  { value: "1h", label: "1H" },
  { value: "30m", label: "30M" },
  { value: "15m", label: "15M" },
  { value: "5m", label: "5M" },
  { value: "1m", label: "1M" },
] as const;

// 周期档时长（分钟，供排序）
export const TF_DURATION: Record<string, number> = {
  "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
  "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480,
  "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080,
};

/** 周期档按时长降序排序（最长在前） */
export function sortTimeframesLongestFirst(tfs: string[]): string[] {
  return [...tfs].sort((a, b) => (TF_DURATION[b] ?? 0) - (TF_DURATION[a] ?? 0));
}

/** 有序周期槽位的角色标签 */
export const TF_LAYER_ROLES = ["趋势", "判断", "确认", "执行"];

// ---------------------------------------------------------------------------
// Indicators
// ---------------------------------------------------------------------------

export const INDICATOR_GROUPS = [
  {
    label: "趋势",
    items: ["EMA", "SMA", "WMA", "DEMA", "TEMA", "VWAP"],
  },
  {
    label: "动量",
    items: ["RSI", "MACD", "Stochastic", "CCI", "ROC", "Williams %R", "Aroon", "CMO", "TRIX"],
  },
  {
    label: "波动",
    items: ["BB", "ATR", "Keltner", "Donchian"],
  },
  {
    label: "成交量",
    items: ["OBV", "CMF", "MFI", "RVOL", "VROC", "AD", "CVD", "VWMA", "VolumeProfile"],
  },
  {
    label: "趋势强度",
    items: ["ADX", "PSAR"],
  },
  {
    label: "形态",
    items: [
      "BearishEngulfing", "EveningStar", "ThreeBlackCrows", "ShootingStar",
      "ThreeWhiteSoldiers", "MorningStar", "BullishReversal", "BearishReversal",
      "BullishDivergence", "BearishDivergence",
    ],
  },
];

export const INDICATOR_FLAT_LIST = [
  "EMA",
  "SMA",
  "RSI",
  "MACD",
  "BB",
  "ATR",
  "ADX",
  "Stochastic",
  "CCI",
  "OBV",
];

// ---------------------------------------------------------------------------
// 指标显示名（缩写+中文名）
// ---------------------------------------------------------------------------

export const INDICATOR_LABELS: Record<string, string> = {
  EMA: "EMA(指数均线)",
  SMA: "SMA(简单均线)",
  WMA: "WMA(加权均线)",
  DEMA: "DEMA(双指数均线)",
  TEMA: "TEMA(三指数均线)",
  VWAP: "VWAP(成交量加权)",
  RSI: "RSI(相对强弱)",
  MACD: "MACD(指数平滑异同)",
  Stochastic: "Stochastic(随机指标)",
  CCI: "CCI(商品通道)",
  ROC: "ROC(变动率)",
  "Williams %R": "WR(威廉指标)",
  Aroon: "Aroon(阿隆)",
  CMO: "CMO(钱德动量)",
  TRIX: "TRIX(三重指数)",
  BB: "BB(布林带)",
  ATR: "ATR(真实波幅)",
  Keltner: "Keltner(肯特纳)",
  Donchian: "Donchian(唐奇安)",
  OBV: "OBV(能量潮)",
  CMF: "CMF(蔡金资金流)",
  MFI: "MFI(资金流量)",
  RVOL: "RVOL(相对成交量)",
  VROC: "VROC(量变动率)",
  AD: "AD(累积分布)",
  CVD: "CVD(累积成交量差)",
  VWMA: "VWMA(量加权均线)",
  VolumeProfile: "VP(成交量分布)",
  ADX: "ADX(平均趋向)",
  PSAR: "PSAR(抛物线指标)",
  BearishEngulfing: "看跌吞没",
  EveningStar: "黄昏之星",
  ThreeBlackCrows: "三只乌鸦",
  ShootingStar: "流星锤",
  ThreeWhiteSoldiers: "三白兵",
  MorningStar: "黎明之星",
  BullishReversal: "阳转反转",
  BearishReversal: "阴转反转",
  BullishDivergence: "底背离",
  BearishDivergence: "顶背离",
};

// ---------------------------------------------------------------------------
// Condition types
// ---------------------------------------------------------------------------

export const CONDITION_TYPE_LABELS: Record<string, string> = {
  lt: "<",
  gt: ">",
  le: "<=",
  ge: ">=",
  cross_above: "金叉",
  cross_below: "死叉",
  price_above: "价格在上方",
  price_below: "价格在下方",
  cross_above_series: "上穿指标线",
  cross_below_series: "下穿指标线",
  lookback_any: "回溯N根满足",
  lookback_all: "回溯N根全满足",
  touch_bounce: "触碰反弹",
  role_reversal: "角色转换",
  wick_touch: "影线触及",
};

export const CONDITION_TYPE_SYMBOLS: Record<string, string> = {
  lt: "<",
  gt: ">",
  le: "<=",
  ge: ">=",
  cross_above: "cross_above",
  cross_below: "cross_below",
  price_above: "price_above",
  price_below: "price_below",
  cross_above_series: "cross_above_series",
  cross_below_series: "cross_below_series",
  lookback_any: "lookback_any",
  lookback_all: "lookback_all",
  touch_bounce: "touch_bounce",
  role_reversal: "role_reversal",
  wick_touch: "wick_touch",
};

export const CONDITION_OPTIONS = [
  { value: "lt", label: "<" },
  { value: "gt", label: ">" },
  { value: "le", label: "<=" },
  { value: "ge", label: ">=" },
  { value: "cross_above", label: "金叉" },
  { value: "cross_below", label: "死叉" },
  { value: "price_above", label: "价格在上方" },
  { value: "price_below", label: "价格在下方" },
  { value: "cross_above_series", label: "上穿指标线" },
  { value: "cross_below_series", label: "下穿指标线" },
  { value: "lookback_any", label: "回溯N根满足" },
  { value: "lookback_all", label: "回溯N根全满足" },
  { value: "touch_bounce", label: "触碰反弹" },
  { value: "role_reversal", label: "角色转换" },
  { value: "wick_touch", label: "影线触及" },
];

// ---------------------------------------------------------------------------
// 评分模板标签（任务详情展示用）
// ---------------------------------------------------------------------------

export const SCORE_TEMPLATE_LABELS: Record<string, string> = {
  explorer: "收益探索",
  optimizer: "稳健收益",
  max_return: "极致收益",
  // 历史别名
  profit_first: "收益优先",
  steady: "稳健优先",
  risk_first: "风控优先",
};

// ---------------------------------------------------------------------------
// 适应度评估的缺省需求
// ---------------------------------------------------------------------------

export const REQUIREMENTS_DEFAULTS = {
  objective: "sharpe" as const,
  min_annual_return: 0.0,
  max_drawdown: 0.30,
  min_win_rate: 0.0,
  min_total_trades: 10,
  min_profit_factor: 1.2,
};

export const OBJECTIVE_OPTIONS = [
  { value: "sharpe", label: "夏普比率" },
  { value: "calmar", label: "卡尔马比率" },
  { value: "annual_return", label: "年化收益率" },
] as const;

export const FITNESS_COLOR_THRESHOLDS: Record<string, { good: number; mid: number }> = {
  sharpe: { good: 1.0, mid: 0.5 },
  calmar: { good: 2.0, mid: 1.0 },
  annual_return: { good: 0.15, mid: 0.08 },
};

// ---------------------------------------------------------------------------
// Task status
// ---------------------------------------------------------------------------

import type { EvolutionTaskStatus } from "@/types/api";

export const STATUS_LABELS: Record<EvolutionTaskStatus, string> = {
  pending: "等待中",
  running: "探索中",
  paused: "已暂停",
  stopped: "已停止",
  completed: "已完成",
};

export function isActiveStatus(status: EvolutionTaskStatus): boolean {
  return status === "running" || status === "pending";
}

// ---------------------------------------------------------------------------
// MTF (Multi-Timeframe) colors
// ---------------------------------------------------------------------------

// 系列色族已迁 chartThemes.ts（白名单单源——fe-asset-data-domain 6.1）；
// 此处 re-export 保消费方 import 路径稳定
import { CHART_INDICATOR_SERIES_COLORS } from "@/components/charts/core/chartThemes";

export {
  MTF_TIMEFRAME_COLORS,
  EMA_DEFAULT_COLORS,
} from "@/components/charts/core/chartThemes";

export const PATTERN_ACTIONS = [
  { value: "divergence_top", label: "顶背离" },
  { value: "divergence_bottom", label: "底背离" },
  { value: "consecutive_up", label: "连涨N根" },
  { value: "consecutive_down", label: "连跌N根" },
  { value: "touch_bounce", label: "触碰反弹" },
  { value: "role_reversal", label: "角色转换" },
] as const;

export const CHART_INDICATOR_DEFAULTS: import("@/types/api").ChartIndicatorConfig = {
  ema_periods: [10, 20, 50],
  ema_colors: CHART_INDICATOR_SERIES_COLORS.ema,
  boll: { enabled: true, period: 20, std: 2.0, color: CHART_INDICATOR_SERIES_COLORS.boll },
  rsi: { enabled: true, period: 14, overbought: 70, oversold: 30 },
  vol: { enabled: true, position: "overlay" },
};

// ---------------------------------------------------------------------------
// Leverage & Direction
// ---------------------------------------------------------------------------

export const LEVERAGE_OPTIONS = [
  { value: 1, label: "1x" },
  { value: 2, label: "2x" },
  { value: 3, label: "3x" },
  { value: 5, label: "5x" },
  { value: 10, label: "10x" },
] as const;

export const DIRECTION_OPTIONS = [
  { value: "long", label: "做多" },
  { value: "short", label: "做空" },
  { value: "mixed", label: "混合探索" },
] as const;

// ---------------------------------------------------------------------------
// 停止原因标签
// ---------------------------------------------------------------------------

export const STOP_REASON_LABELS: Record<string, string> = {
  target_reached: "达到目标分数",
  stagnation: "长期无改善(停滞)",
  decline: "分数持续下降",
  max_generations: "达到最大代数",
  user_stop: "用户手动停止",
  error: "运行异常",
  invalid_dna: "无效种子DNA",
};
