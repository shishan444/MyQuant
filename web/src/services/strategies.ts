import { api } from "./api";
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

export interface VerifyProgressEvent {
  current: number;
  total: number;
  group: string;
  range_start: string;
  range_end: string;
  batch_results: Array<Record<string, unknown>>;
}

export interface VerifyStreamCallbacks {
  onProgress: (event: VerifyProgressEvent) => void;
  onComplete: (data: { session_id: string; summary: unknown[]; results: unknown[] }) => void;
  onError: (message: string) => void;
}

export interface BatchBacktestStreamCallbacks {
  onProgress: (event: VerifyProgressEvent) => void;
  onComplete: (data: { summary: unknown[]; results: unknown[] }) => void;
  onError: (message: string) => void;
}

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
  const baseUrl = import.meta.env.VITE_API_BASE_URL || "";
  fetch(`${baseUrl}/api/strategies/verify/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        callbacks.onError(text || `HTTP ${response.status}`);
        return;
      }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // 定义在循环外：流结束时残留 buffer 的事件也能被处理（此前定义在 while 内导致末尾事件被静默丢弃）
      const processPart = (part: string) => {
        if (!part.trim()) return;
        const lines = part.split("\n");
        const eventType = lines.find((l) => l.startsWith("event: "))?.slice(7);
        const dataRaw = lines.find((l) => l.startsWith("data: "))?.slice(6);
        if (!eventType || !dataRaw) return;
        try {
          const data = JSON.parse(dataRaw);
          if (eventType === "progress") callbacks.onProgress(data);
          else if (eventType === "complete") callbacks.onComplete(data);
          else if (eventType === "error") callbacks.onError(data.message || "Unknown error");
        } catch { /* ignore parse errors */ }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop()!;

        for (const part of parts) processPart(part);
      }

      // Process any remaining data in buffer after stream ends
      if (buffer.trim()) processPart(buffer);
    })
    .catch((err) => {
      if (err.name !== "AbortError") callbacks.onError(err.message);
    });
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
  const baseUrl = import.meta.env.VITE_API_BASE_URL || "";
  fetch(`${baseUrl}/api/strategies/batch-backtest/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        callbacks.onError(text || `HTTP ${response.status}`);
        return;
      }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // 定义在循环外：流结束时残留 buffer 的事件也能被处理（与 verify 流同修）
      const processPart = (part: string) => {
        if (!part.trim()) return;
        const lines = part.split("\n");
        const eventType = lines.find((l) => l.startsWith("event: "))?.slice(7);
        const dataRaw = lines.find((l) => l.startsWith("data: "))?.slice(6);
        if (!eventType || !dataRaw) return;
        try {
          const data = JSON.parse(dataRaw);
          if (eventType === "progress") callbacks.onProgress(data);
          else if (eventType === "complete") callbacks.onComplete(data);
          else if (eventType === "error") callbacks.onError(data.message || "Unknown error");
        } catch { /* ignore */ }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop()!;

        for (const part of parts) processPart(part);
      }
      if (buffer.trim()) processPart(buffer);
    })
    .catch((err) => {
      if (err.name !== "AbortError") callbacks.onError(err.message);
    });
}

export async function fetchBatchBacktestDetail(resultId: string): Promise<BacktestResult> {
  const { data } = await api.get(`/api/strategies/batch-backtest/${resultId}`);
  return data;
}
