import { api } from "@/services/api";
import type { components } from "@/types/generated/api";

/**
 * 01 观察工作台服务层（workbench-observation 前端组）。
 * 两端点消费：pending-events 聚合+validation-records 列表；
 * 既有面（快照/假设/分析任务/日志）沿各自域服务。
 * 响应类型消费 OpenAPI 生成 schema（types/api.ts 同款 S 形制——
 * fe-type-contract-consolidation R-5：手写 interface 与
 * `as unknown as` 双重断言退役；契约调整改后端模型→
 * `npm run generate:types` 再生成，本层随 tsc 暴露差异）。
 */
type S = components["schemas"];

/** 待裁决事件 ← 后端 PendingEvent；type 三族收窄为判别联合（后端
 *  str 松形制——观察面聚合不立三套模型；Omit 语义收窄先例
 *  types/api.ts ConditionInput 同款） */
export type PendingEvent = Omit<S["PendingEvent"], "type"> & {
  type: "union_exhausted" | "qualify_pending" | "trigger_deviation";
};

/** 事件源可用性 ← 后端 Availability（生成 schema 直消费；三键
 *  Optional——None=可用，"unavailable"+reason=源缺失） */
export type PendingEventsAvailability = S["Availability"];

/** 待裁决事件聚合响应 ← 后端 PendingEventsResponse；events 行类型
 *  收窄（元素 type 判别联合）。字段语义：scanned_tasks=本轮实际
 *  扫描终态任务数（CARD-07 披露）；scanned_running_tasks=本轮实际
 *  扫描运行中任务数（批次三补件——qualify 半边同界披露）；
 *  truncated=达扫描上限（1000，终态或运行中任一面）——可能漏扫的
 *  显式告知 */
export type PendingEventsResponse = Omit<S["PendingEventsResponse"], "events"> & {
  events: PendingEvent[];
};

/** 验证记录行 ← 后端 ValidationRecordRow；verdict 收窄为判别联合
 *  （后端 str 松形制）。statement=假设原话（列表 LEFT JOIN
 *  research_hypotheses——无关联 null，upgrade-validation-records-list
 *  B1.2；adhoc/假设已删如实 null） */
export type ValidationRecordRow = Omit<S["ValidationRecordRow"], "verdict"> & {
  verdict: "pass" | "fail" | "inconclusive";
};

/** 验证记录列表响应 ← 后端 ValidationRecordsResponse；records 行类型
 *  收窄（元素 verdict 判别联合）。verdict_counts=verdict 计数三键恒
 *  在场（与 total 同基数——六维过滤口径；后端 Dict[str,int] 生成物
 *  为索引签名，消费位 `?? 0` 防御沿用）；unsortable_count=
 *  sort=total_return_desc 时无收益值记录计数（落尾披露）；其他 sort
 *  恒 0 */
export type ValidationRecordsResponse = Omit<S["ValidationRecordsResponse"], "records"> & {
  records: ValidationRecordRow[];
};

export async function getPendingEvents(): Promise<PendingEventsResponse> {
  return api.get("/api/workbench/pending-events");
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
  return api.get("/api/research/validation-records", { params });
}
