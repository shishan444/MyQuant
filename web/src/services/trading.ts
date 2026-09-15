/**
 * Trading 域 service——类型全部来自 types/api 别名层（生成源派生），本文件不含独立形状定义。
 */
import { api } from "./api";
import type {
  CreateTradingTaskParams,
  EquitySnapshotList,
  PaperTradeList,
  TradingMetrics,
  TradingRunnerStatus,
  TradingTask,
  TradingTaskList,
} from "@/types/api";

export type {
  CreateTradingTaskParams,
  EquitySnapshot,
  EquitySnapshotList,
  PaperTrade,
  PaperTradeList,
  TradingMetrics,
  TradingRunnerStatus,
  TradingTask,
  TradingTaskList,
} from "@/types/api";

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

export async function getTradingRunnerStatus(): Promise<TradingRunnerStatus> {
  const { data } = await api.get("/api/trading/runner-status");
  return data;
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
