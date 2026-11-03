/**
 * 验证记录浏览数据服务（fe-validation-records-page）：
 * records 列表四维过滤+detail 详情两态（detail_available=false 空骨架如实）。
 * 行形制实测 2026-11-03（records/total/offset/limit/has_more 五键；行十一字段）。
 */
import { api } from "./api";
// 行类型复用 workbench 侧既有定义（列表端点同源——契约三：不建第二份类型权威）
export {
  getValidationRecords as getValidationRecordsPage,
  type ValidationRecordRow,
  type ValidationRecordsResponse,
} from "./workbench";

export const VALIDATION_LEVELS = [
  "isolated_backtest",
  "random_window",
  "staged_validation",
  "composite",
  "component",
] as const;
export const VALIDATION_VERDICTS = ["pass", "fail", "inconclusive"] as const;

/** detail 两态：detail_available=false → executions=[] 空骨架（历史无留痕如实） */
export interface VrTrade {
  action?: string;
  side?: string | null;
  time?: string;
  price?: number;
  size?: number;
  decision?: Record<string, unknown> | null;
}
export interface VrExecution {
  execution_id?: string;
  started_at?: string;
  finished_at?: string;
  status?: string;
  trades?: VrTrade[];
  equity?:
    | { timestamps?: number[]; values?: number[] }
    | { start?: number; length?: number; timeframe?: string };
}
export interface ValidationDetailResponse {
  record_id: string;
  hypothesis_id: string;
  strategy_id: string | null;
  level: string;
  verdict: string;
  created_at: string;
  init_cash: number | null;
  fee: number | null;
  slippage: number | null;
  detail_available: boolean;
  executions: VrExecution[];
}
export async function getValidationRecordDetail(
  recordId: string,
): Promise<ValidationDetailResponse> {
  return api.get(`/api/research/validation-records/${recordId}/detail`);
}
