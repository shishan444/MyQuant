import type { BacktestResult, ChartIndicatorsResponse, DNA } from "@/types/api";

// ---------------------------------------------------------------------------
// Mock: OHLCV candle data
// ---------------------------------------------------------------------------

export const mockOhlcvData = {
  dataset_id: "test-dataset",
  data: [
    { timestamp: "2025-01-01T00:00:00Z", open: 100, high: 110, low: 95, close: 105, volume: 1000 },
    { timestamp: "2025-01-01T04:00:00Z", open: 105, high: 115, low: 100, close: 112, volume: 1200 },
    { timestamp: "2025-01-01T08:00:00Z", open: 112, high: 120, low: 108, close: 118, volume: 900 },
    { timestamp: "2025-01-01T12:00:00Z", open: 118, high: 125, low: 115, close: 120, volume: 1100 },
    { timestamp: "2025-01-01T16:00:00Z", open: 120, high: 130, low: 118, close: 128, volume: 1500 },
  ],
};

// ---------------------------------------------------------------------------
// Mock: Chart indicators response
// ---------------------------------------------------------------------------

export const mockIndicatorResponse: ChartIndicatorsResponse = {
  ema: {
    "10": [
      { time: "2025-01-01T00:00:00Z", value: 100 },
      { time: "2025-01-01T04:00:00Z", value: 103 },
      { time: "2025-01-01T08:00:00Z", value: 108 },
      { time: "2025-01-01T12:00:00Z", value: 113 },
      { time: "2025-01-01T16:00:00Z", value: 118 },
    ],
    "20": [
      { time: "2025-01-01T00:00:00Z", value: 99 },
      { time: "2025-01-01T04:00:00Z", value: 101 },
      { time: "2025-01-01T08:00:00Z", value: 104 },
      { time: "2025-01-01T12:00:00Z", value: 107 },
      { time: "2025-01-01T16:00:00Z", value: 111 },
    ],
  },
  boll: {
    upper: [
      { time: "2025-01-01T00:00:00Z", value: 108 },
      { time: "2025-01-01T04:00:00Z", value: 112 },
      { time: "2025-01-01T08:00:00Z", value: 118 },
      { time: "2025-01-01T12:00:00Z", value: 122 },
      { time: "2025-01-01T16:00:00Z", value: 130 },
    ],
    middle: [
      { time: "2025-01-01T00:00:00Z", value: 100 },
      { time: "2025-01-01T04:00:00Z", value: 104 },
      { time: "2025-01-01T08:00:00Z", value: 110 },
      { time: "2025-01-01T12:00:00Z", value: 114 },
      { time: "2025-01-01T16:00:00Z", value: 122 },
    ],
    lower: [
      { time: "2025-01-01T00:00:00Z", value: 92 },
      { time: "2025-01-01T04:00:00Z", value: 96 },
      { time: "2025-01-01T08:00:00Z", value: 102 },
      { time: "2025-01-01T12:00:00Z", value: 106 },
      { time: "2025-01-01T16:00:00Z", value: 114 },
    ],
  },
  rsi: [
    { time: "2025-01-01T00:00:00Z", value: 55 },
    { time: "2025-01-01T04:00:00Z", value: 62 },
    { time: "2025-01-01T08:00:00Z", value: 68 },
    { time: "2025-01-01T12:00:00Z", value: 72 },
    { time: "2025-01-01T16:00:00Z", value: 75 },
  ],
  macd: {
    macd: [
      { time: "2025-01-01T00:00:00Z", value: 1.2 },
      { time: "2025-01-01T04:00:00Z", value: 1.5 },
      { time: "2025-01-01T08:00:00Z", value: 1.8 },
      { time: "2025-01-01T12:00:00Z", value: 2.0 },
      { time: "2025-01-01T16:00:00Z", value: 2.3 },
    ],
    signal: [
      { time: "2025-01-01T00:00:00Z", value: 1.0 },
      { time: "2025-01-01T04:00:00Z", value: 1.2 },
      { time: "2025-01-01T08:00:00Z", value: 1.4 },
      { time: "2025-01-01T12:00:00Z", value: 1.6 },
      { time: "2025-01-01T16:00:00Z", value: 1.8 },
    ],
    histogram: [
      { time: "2025-01-01T00:00:00Z", value: 0.2 },
      { time: "2025-01-01T04:00:00Z", value: 0.3 },
      { time: "2025-01-01T08:00:00Z", value: 0.4 },
      { time: "2025-01-01T12:00:00Z", value: 0.4 },
      { time: "2025-01-01T16:00:00Z", value: 0.5 },
    ],
  },
  kdj: {
    k: [
      { time: "2025-01-01T00:00:00Z", value: 60 },
      { time: "2025-01-01T04:00:00Z", value: 65 },
      { time: "2025-01-01T08:00:00Z", value: 70 },
      { time: "2025-01-01T12:00:00Z", value: 75 },
      { time: "2025-01-01T16:00:00Z", value: 80 },
    ],
    d: [
      { time: "2025-01-01T00:00:00Z", value: 55 },
      { time: "2025-01-01T04:00:00Z", value: 58 },
      { time: "2025-01-01T08:00:00Z", value: 62 },
      { time: "2025-01-01T12:00:00Z", value: 66 },
      { time: "2025-01-01T16:00:00Z", value: 72 },
    ],
    j: [
      { time: "2025-01-01T00:00:00Z", value: 70 },
      { time: "2025-01-01T04:00:00Z", value: 79 },
      { time: "2025-01-01T08:00:00Z", value: 86 },
      { time: "2025-01-01T12:00:00Z", value: 93 },
      { time: "2025-01-01T16:00:00Z", value: 96 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Mock: chartSettings store default state
// ---------------------------------------------------------------------------

export const mockChartSettings = {
  emaList: [
    { period: 10, color: "#3B82F6", enabled: true },
    { period: 20, color: "#10B981", enabled: true },
    { period: 50, color: "#F59E0B", enabled: false },
  ],
  boll: { enabled: true, period: 20, std: 2.0, color: "#F59E0B" },
  rsi: { enabled: true, period: 14, overbought: 70, oversold: 30 },
  vol: { enabled: true, position: "overlay" as const },
};

// ---------------------------------------------------------------------------
// Mock: DNA
// ---------------------------------------------------------------------------

export const mockDNA: DNA = {
  signal_genes: [
    {
      indicator: "ema",
      params: { period: 10 },
      role: "entry_trigger",
      condition: { type: "cross_above", value: 20 },
    },
  ],
  logic_genes: { entry_logic: "AND", exit_logic: "OR" },
  execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
  risk_genes: { stop_loss: 0.05, take_profit: 0.1, position_size: 0.3, leverage: 1, direction: "long" },
  generation: 0,
  parent_ids: [],
  mutation_ops: [],
};

// ---------------------------------------------------------------------------
// Mock: BacktestResult
// ---------------------------------------------------------------------------

export const mockBacktestResult: BacktestResult = {
  result_id: "test-result-001",
  strategy_id: "test-strategy-001",
  symbol: "BTCUSDT",
  timeframe: "4h",
  data_start: "2025-01-01T00:00:00Z",
  data_end: "2025-03-01T00:00:00Z",
  init_cash: 100000,
  fee: 0.001,
  slippage: 0.0005,
  total_return: 0.255,
  sharpe_ratio: 1.5,
  max_drawdown: -0.083,
  win_rate: 0.6,
  total_trades: 10,
  total_score: 72,
  fitness: 0.68,
  qualified: true,
  template_name: "default",
  run_source: "lab",
  total_funding_cost: 0,
  liquidated: false,
  signals: [
    { type: "buy" as const, timestamp: "2025-01-05T08:00:00Z", price: 100, reason: "entry" },
    { type: "sell" as const, timestamp: "2025-01-10T12:00:00Z", price: 110, reason: "exit" },
  ],
  equity_curve: [
    { timestamp: "2025-01-01T00:00:00Z", value: 100000 },
    { timestamp: "2025-01-10T00:00:00Z", value: 105000 },
    { timestamp: "2025-02-01T00:00:00Z", value: 125500 },
  ],
};

// ---------------------------------------------------------------------------
// Mock: Trading types
// ---------------------------------------------------------------------------

export const mockTradingMetrics = {
  task_id: "t1",
  total_return: 2000,
  total_return_pct: 2.0,
  win_rate: 0.6,
  profit_factor: 1.8,
  max_drawdown: 800,
  max_drawdown_pct: 0.8,
  avg_trade_pnl: 400,
  total_trades: 5,
  total_pnl: 2000,
  realized_pnl: 2000,
  unrealized_pnl: 0,
  win_count: 3,
  loss_count: 2,
};

export const mockPaperTrade = {
  id: 1,
  task_id: "t1",
  bar_time: "2025-01-05T08:00:00Z",
  side: "long",
  action: "open",
  price: 100000,
  quantity: 0.1,
  pnl: null,
  fee_paid: 10,
  reason: null,
};

export const mockEquitySnapshots = [
  { timestamp: "2025-01-01T00:00:00Z", equity: 100000, balance: 100000, unrealized_pnl: 0, position_side: "flat" },
  { timestamp: "2025-01-05T08:00:00Z", equity: 102000, balance: 100000, unrealized_pnl: 2000, position_side: "long" },
  { timestamp: "2025-01-10T12:00:00Z", equity: 105000, balance: 105000, unrealized_pnl: 0, position_side: "flat" },
];
