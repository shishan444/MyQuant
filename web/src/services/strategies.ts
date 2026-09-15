import { api } from "./api";
import { streamSse, type StreamHandlers } from "@/realtime/sse";
import type {
  Strategy,
  StrategyListResponse,
  BacktestResult,
  VerifyResponse,
  VerifyHistoryResponse,
  VerifySessionListResponse,
} from "@/types/api";

export async function getStrategies(params?: {
  symbol?: string;
  source?: string;
  tags?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}): Promise<StrategyListResponse> {
  const { data } = await api.get("/api/strategies", { params });
  return data;
}

export async function getStrategy(id: string): Promise<Strategy> {
  const { data } = await api.get(`/api/strategies/${id}`);
  return data;
}

export async function createStrategy(payload: {
  name?: string;
  dna: unknown;
  symbol: string;
  timeframe: string;
  source: string;
  source_task_id?: string;
  tags?: string;
  notes?: string;
}): Promise<Strategy> {
  const { data } = await api.post("/api/strategies", payload);
  return data;
}

export async function updateStrategy(
  id: string,
  payload: Partial<{ name: string; tags: string; notes: string }>
): Promise<Strategy> {
  const { data } = await api.put(`/api/strategies/${id}`, payload);
  return data;
}

export async function deleteStrategy(id: string): Promise<void> {
  await api.delete(`/api/strategies/${id}`);
}

export async function runBacktest(payload: {
  dna: unknown;
  symbol: string;
  timeframe: string;
  dataset_id?: string;
  score_template?: string;
  init_cash?: number;
  fee?: number;
  slippage?: number;
  data_start?: string;
  data_end?: string;
  timeframe_pool?: string[];
}): Promise<BacktestResult> {
  const { data } = await api.post("/api/strategies/backtest", payload, {
    timeout: 60000,
  });
  return data;
}

export async function compareStrategies(payload: {
  strategy_ids: string[];
  dataset_id?: string;
  score_template?: string;
}): Promise<{ results: BacktestResult[] }> {
  const { data } = await api.post("/api/strategies/compare", payload);
  return data;
}

export async function verifyStrategies(payload: {
  strategy_ids: string[];
  data_ranges: Array<{ start: string; end: string }>;
  init_cash?: number;
  fee?: number;
  slippage?: number;
}): Promise<VerifyResponse> {
  const { data } = await api.post("/api/strategies/verify", payload, {
    timeout: 300000,
  });
  return data;
}

export async function getVerifyHistory(params?: {
  strategy_id?: string;
  limit?: number;
}): Promise<VerifyHistoryResponse> {
  const { data } = await api.get("/api/strategies/verify/history", { params });
  return data;
}

export async function getVerifySessions(limit?: number): Promise<VerifySessionListResponse> {
  const { data } = await api.get("/api/strategies/verify/sessions", { params: { limit } });
  return data;
}

export async function getSessionResults(sessionId: string): Promise<VerifyHistoryResponse> {
  const { data } = await api.get(`/api/strategies/verify/sessions/${sessionId}/results`);
  return data;
}

// 流式回调契约类型已收编 realtime 层（src/realtime/sse.ts），此处 re-export 保消费方零改动
export type { StreamProgressEvent as VerifyProgressEvent } from "@/realtime/sse";
export type VerifyStreamCallbacks = StreamHandlers<{ session_id: string; summary: unknown[]; results: unknown[] }>;
export type BatchBacktestStreamCallbacks = StreamHandlers<{ summary: unknown[]; results: unknown[] }>;

export function verifyStrategiesStream(
  payload: {
    strategy_ids: string[];
    data_ranges: Array<{ start: string; end: string }>;
    init_cash?: number;
    fee?: number;
    slippage?: number;
    leverage?: number;
  },
  callbacks: VerifyStreamCallbacks,
  signal?: AbortSignal,
): void {
  streamSse("/api/strategies/verify/stream", payload, callbacks, signal);
}

export function batchBacktestStream(
  payload: {
    strategy_ids: string[];
    data_ranges: Array<{ start: string; end: string }>;
    init_cash?: number;
    fee?: number;
    slippage?: number;
    leverage?: number;
  },
  callbacks: BatchBacktestStreamCallbacks,
  signal?: AbortSignal,
): void {
  streamSse("/api/strategies/batch-backtest/stream", payload, callbacks, signal);
}

export async function fetchBatchBacktestDetail(resultId: string): Promise<BacktestResult> {
  const { data } = await api.get(`/api/strategies/batch-backtest/${resultId}`);
  return data;
}
