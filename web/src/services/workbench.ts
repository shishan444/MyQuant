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
  /** 本轮实际扫描运行中任务数（批次三补件——qualify 半边同界披露） */
  scanned_running_tasks: number;
  /** 达扫描上限（1000，终态或运行中任一面）——可能漏扫的显式告知 */
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
  /** 假设原话（列表 LEFT JOIN research_hypotheses——无关联 null，
   *  upgrade-validation-records-list B1.2；adhoc/假设已删如实 null） */
  statement: string | null;
}

export interface ValidationRecordsResponse {
  records: ValidationRecordRow[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  /** verdict 计数三键恒在场（与 total 同基数——六维过滤口径） */
  verdict_counts: { pass: number; fail: number; inconclusive: number };
  /** sort=total_return_desc 时无收益值记录计数（落尾披露）；其他 sort 恒 0 */
  unsortable_count: number;
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
  /** 排序封闭枚举四值（time_desc 缺省/time_asc/total_return_desc/
   *  verdict_quality——服务端执行，upgrade-validation-records-list L4） */
  sort?: string;
  /** statement 关键词（LIKE %q%、通配符字面化——L2） */
  q?: string;
  /** 泛化过滤（true=仅跨标的记录，与 level 取交集——⑦终裁 toggle） */
  generalization?: boolean;
  limit?: number;
  offset?: number;
}): Promise<ValidationRecordsResponse> {
  const res = await api.get("/api/research/validation-records", { params });
  return res as unknown as ValidationRecordsResponse;
}
