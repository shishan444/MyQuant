/**
 * 验证记录浏览数据服务（fe-validation-records-page + Q1 修复）：
 * records 列表四维过滤+detail 详情两态（detail_available=false 空骨架如实）。
 * 行形制实测 2026-11-03（records/total/offset/limit/has_more 五键；行十一字段）。
 * executions 形制按后端源码修正（validation_detail.py+research.py
 * get_validation_executions——fix/fe-validation-forms-remediation Q1：
 * trades 为 entry/exit 笔对形制、equity 恒带 values、grid_aligned 区分
 * compact〔timestamps=null+start/length/timeframe 重建〕与全时间戳两形制）。
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

/** 逐笔形制（后端笔对形：entry/exit 对+direction 定买卖语义） */
export interface VrTrade {
  seq: number;
  entry_time: string;
  exit_time: string;
  entry_price: number | null;
  exit_price: number | null;
  size: number | null;
  direction: string | null;
  pnl: number | null;
}

/** equity 双形制：values 恒在；grid_aligned=true 为 compact（timestamps=null，
 *  前端按 start+length×timeframe 重建时间轴）；false 为全时间戳直用 */
export interface VrEquity {
  grid_aligned: boolean;
  timeframe: string;
  start?: string | null;
  length?: number | null;
  timestamps?: string[] | null;
  values: number[];
}

export interface VrExecution {
  exec_ref: string;
  exec_index: number;
  range_start: string;
  range_end: string;
  trades: VrTrade[];
  equity: VrEquity;
}

/** detail 两态：detail_available=false → executions=[] 空骨架（历史无留痕如实） */
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
