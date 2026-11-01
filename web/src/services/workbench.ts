import { api } from "@/services/api";

/**
 * 01 观察工作台服务层（workbench-observation 前端组）。
 * 两端点消费：pending-events 聚合+validation-records 列表；
 * 既有面（快照/假设/分析任务/日志）沿各自域服务。
 */

export interface PendingEvent {
  type: "union_exhausted" | "qualify_pending" | "trigger_deviation";
  payload: Record<string, unknown>;
}

export interface PendingEventsResponse {
  events: PendingEvent[];
  degraded: number;
  availability: {
    qualify_pending: string | null;
    trigger_deviation: string | null;
    reason: string | null;
  };
  /** 本轮实际扫描终态任务数（CARD-07 披露） */
  scanned_tasks: number;
  /** 达扫描上限（1000）——可能漏扫的显式告知 */
  truncated: boolean;
}

export interface ValidationRecordRow {
  record_id: string;
  hypothesis_id: string;
  strategy_id: string | null;
  level: string;
  segmentation: string;
  verdict: "pass" | "fail" | "inconclusive";
  range_hash: string;
  pollution_cause: string | null;
  created_at: string;
  params: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
}

export interface ValidationRecordsResponse {
  records: ValidationRecordRow[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export async function getPendingEvents(): Promise<PendingEventsResponse> {
  const res = await api.get("/api/workbench/pending-events");
  return res as unknown as PendingEventsResponse;
}

export async function getValidationRecords(params?: {
  hypothesis_id?: string;
  strategy_id?: string;
  level?: string;
  verdict?: string;
  limit?: number;
  offset?: number;
}): Promise<ValidationRecordsResponse> {
  const res = await api.get("/api/research/validation-records", { params });
  return res as unknown as ValidationRecordsResponse;
}
