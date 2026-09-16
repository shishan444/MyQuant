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
    return api.get("/api/trading/tasks", { params });
}

export async function getTradingTask(taskId: string): Promise<TradingTask> {
    return api.get(`/api/trading/tasks/${taskId}`);
}

export async function createTradingTask(
  params: CreateTradingTaskParams
): Promise<TradingTask> {
    return api.post("/api/trading/tasks", { json: params });
}

export async function stopTradingTask(taskId: string): Promise<TradingTask> {
    return api.post(`/api/trading/tasks/${taskId}/stop`);
}

export async function restartTradingTask(taskId: string): Promise<TradingTask> {
    return api.post(`/api/trading/tasks/${taskId}/restart`);
}

export async function getTradingTrades(
  taskId: string,
  limit?: number
): Promise<PaperTradeList> {
    return api.get(`/api/trading/tasks/${taskId}/trades`, {
    params: { limit },
  });
}

export async function getTradingRunnerStatus(): Promise<TradingRunnerStatus> {
    return api.get("/api/trading/runner-status");
}

export async function getTradingEquity(taskId: string): Promise<EquitySnapshotList> {
    return api.get(`/api/trading/tasks/${taskId}/equity`);
}

export async function getTradingMetrics(taskId: string): Promise<TradingMetrics> {
    return api.get(`/api/trading/tasks/${taskId}/metrics`);
}

export async function deleteTradingTask(taskId: string): Promise<{ deleted: boolean }> {
    return api.delete(`/api/trading/tasks/${taskId}`);
}
