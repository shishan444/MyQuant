import { api } from "./api";

export interface TradingTask {
  task_id: string;
  status: string;
  strategy_name: string | null;
  symbol: string;
  timeframe: string;
  initial_cash: number;
  fee: number;
  leverage: number;
  direction: string;
  score_template: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  stopped_at: string | null;
  stop_reason: string | null;
  position_side: string | null;
  position_entry: number | null;
  position_quantity: number | null;
  position_margin: number | null;
  position_funding: number | null;
  balance: number | null;
  unrealized_pnl: number;
  total_trades: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  last_bar_time: string | null;
  last_bar_close: number | null;
  confidence_sizing_enabled: boolean;
}

export interface TradingTaskList {
  tasks: TradingTask[];
  total: number;
}

export interface PaperTrade {
  id: number;
  task_id: string;
  bar_time: string;
  side: string;
  action: string;
  price: number;
  quantity: number;
  pnl: number | null;
  fee_paid: number;
  reason: string | null;
}

export interface PaperTradeList {
  trades: PaperTrade[];
  total: number;
}

export interface CreateTradingTaskParams {
  dna_json: string;
  symbol?: string;
  timeframe?: string;
  initial_cash?: number;
  fee?: number;
  leverage?: number;
  direction?: string;
  score_template?: string;
  strategy_name?: string;
  confidence_sizing_enabled?: boolean;
}

export async function listTradingTasks(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<TradingTaskList> {
  const { data } = await api.get("/api/trading/tasks", { params });
  return data;
}

export async function getTradingTask(taskId: string): Promise<TradingTask> {
  const { data } = await api.get(`/api/trading/tasks/${taskId}`);
  return data;
}

export async function createTradingTask(
  params: CreateTradingTaskParams
): Promise<TradingTask> {
  const { data } = await api.post("/api/trading/tasks", params);
  return data;
}

export async function stopTradingTask(taskId: string): Promise<TradingTask> {
  const { data } = await api.post(`/api/trading/tasks/${taskId}/stop`);
  return data;
}

export async function restartTradingTask(taskId: string): Promise<TradingTask> {
  const { data } = await api.post(`/api/trading/tasks/${taskId}/restart`);
  return data;
}

export async function getTradingTrades(
  taskId: string,
  limit?: number
): Promise<PaperTradeList> {
  const { data } = await api.get(`/api/trading/tasks/${taskId}/trades`, {
    params: { limit },
  });
  return data;
}

export async function getTradingRunnerStatus(): Promise<{
  is_alive: boolean;
  active_task_id: string | null;
}> {
  const { data } = await api.get("/api/trading/runner-status");
  return data;
}

export interface EquitySnapshot {
  id: number;
  task_id: string;
  timestamp: string;
  equity: number;
  balance: number;
  unrealized_pnl: number;
  position_side: string;
}

export interface EquitySnapshotList {
  snapshots: EquitySnapshot[];
  total: number;
}

export interface TradingMetrics {
  task_id: string;
  total_return: number;
  total_return_pct: number;
  win_rate: number;
  profit_factor: number | null;
  max_drawdown: number;
  max_drawdown_pct: number;
  avg_trade_pnl: number;
  total_trades: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
}

export async function getTradingEquity(taskId: string): Promise<EquitySnapshotList> {
  const { data } = await api.get(`/api/trading/tasks/${taskId}/equity`);
  return data;
}

export async function getTradingMetrics(taskId: string): Promise<TradingMetrics> {
  const { data } = await api.get(`/api/trading/tasks/${taskId}/metrics`);
  return data;
}

export async function deleteTradingTask(taskId: string): Promise<{ deleted: boolean }> {
  const { data } = await api.delete(`/api/trading/tasks/${taskId}`);
  return data;
}
