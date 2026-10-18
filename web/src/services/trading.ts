/**
 * Trading 域 service——类型全部来自 types/api 别名层（生成源派生），本文件不含独立形状定义。
 * 端点族 = /api/paper-trading/*（rebuild-paper-trading 新域十二端点，api/routes/paper_trading.py）。
 */
import { api } from "./api";
import type {
  CreateTradingTaskParams,
  EquitySnapshotList,
  FundingRecordList,
  PaperFillList,
  TradingFinalReport,
  TradingReport,
  TradingRunnerStatus,
  TradingTask,
  TradingTaskList,
} from "@/types/api";

export type {
  CreateTradingTaskParams,
  EquitySnapshot,
  EquitySnapshotList,
  FundingRecord,
  FundingRecordList,
  PaperFill,
  PaperFillList,
  TradingFinalReport,
  TradingReport,
  TradingReportDeviation,
  TradingReportEvidence,
  TradingReportPerformance,
  TradingRunnerStatus,
  TradingTask,
  TradingTaskList,
} from "@/types/api";

/** 流水面（fills/equity/funding）分页参数——limit+offset 探测式分页（has_more） */
export interface ListPageParams {
  limit?: number;
  offset?: number;
}

// -- 任务族 --

export async function listTradingTasks(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<TradingTaskList> {
    return api.get("/api/paper-trading/tasks", { params });
}

export async function getTradingTask(taskId: string): Promise<TradingTask> {
  return api.get(`/api/paper-trading/tasks/${taskId}`);
}

export async function createTradingTask(
  params: CreateTradingTaskParams
): Promise<TradingTask> {
    return api.post("/api/paper-trading/tasks", { json: params });
}

/** stop 三态分派（pending 直迁 stopped / running 协作停 / 终态 409） */
export async function stopTradingTask(taskId: string): Promise<TradingTask> {
  return api.post(`/api/paper-trading/tasks/${taskId}/stop`);
}

/** 任意终态克隆重跑（新任务编号 pending 行；运行/pending→409 stop first） */
export async function cloneTradingTask(taskId: string): Promise<TradingTask> {
  return api.post(`/api/paper-trading/tasks/${taskId}/clone`);
}

/** 删除任务（仅终态五表级联；运行/pending→409 stop first） */
export async function deleteTradingTask(taskId: string): Promise<{ deleted: boolean }> {
  return api.delete(`/api/paper-trading/tasks/${taskId}`);
}

// -- 明细流水族 --

export async function getTradingFills(
  taskId: string,
  params?: ListPageParams
): Promise<PaperFillList> {
    return api.get(`/api/paper-trading/tasks/${taskId}/fills`, {
    params: { limit: params?.limit, offset: params?.offset },
  });
}

export async function getTradingEquity(
  taskId: string,
  params?: ListPageParams
): Promise<EquitySnapshotList> {
    return api.get(`/api/paper-trading/tasks/${taskId}/equity`, {
    params: { limit: params?.limit, offset: params?.offset },
  });
}

export async function getTradingFunding(
  taskId: string,
  params?: ListPageParams
): Promise<FundingRecordList> {
    return api.get(`/api/paper-trading/tasks/${taskId}/funding`, {
    params: { limit: params?.limit, offset: params?.offset },
  });
}

// -- 报告族 --

/** 滚动报告（任意状态可取——running 中随时取当前版，三层退化自解释） */
export async function getTradingReport(taskId: string): Promise<TradingReport> {
  return api.get(`/api/paper-trading/tasks/${taskId}/report`);
}

/** 结题固化版（未结题 409；version 缺省最新版、?version=1 取结题初版） */
export async function getTradingFinalReport(
  taskId: string,
  version?: number
): Promise<TradingFinalReport> {
    return api.get(`/api/paper-trading/tasks/${taskId}/report/final`, {
    params: { version },
  });
}

// -- 调度器 --

export async function getTradingRunnerStatus(): Promise<TradingRunnerStatus> {
  return api.get("/api/paper-trading/runner-status");
}
