/**
 * 验证记录浏览数据服务（fe-validation-records-page + Q1 修复 +
 * upgrade-validation-records-list B2 前端原子面）：
 * records 列表检索（sort/q/generalization——六维过滤下推）+detail
 * 详情两态（detail_available=false 空骨架如实）+union-balance 余量。
 * 行形制实测 2026-11-03；executions 形制按后端源码修正
 * （validation_detail.py+research.py——trades 为 entry/exit 笔对形制、
 * equity 恒带 values、grid_aligned 区分 compact〔timestamps=null+
 * start/length/timeframe 重建〕与全时间戳两形制）。
 * B2 增量（add-vr-signal-trace 契约消费）：记录级三布尔+mtf_gated_bars；
 * 份级 trail_series（trail 档 [{time,value}] 非 trail 档 null）与
 * trace_rows（信号留痕行——seq 升序）。
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

/** 列表排序封闭枚举（L4 三键控件映射；time_asc 后端支持前端不暴露） */
export const VALIDATION_SORTS = [
  "time_desc",
  "total_return_desc",
  "verdict_quality",
] as const;
export type ValidationSort = (typeof VALIDATION_SORTS)[number];

/** 类型筛选三枚（中文标签——R-021 b 形制；composite/component 死值域不进枚举） */
export const VALIDATION_TYPE_OPTIONS = [
  { value: "isolated_backtest", label: "隔离验证" },
  { value: "random_window", label: "随机窗口" },
  { value: "staged_validation", label: "分段验证" },
] as const;

/** 逐笔形制（后端笔对形：entry/exit 对+direction 定买卖语义；
 *  exit_time null=未平仓如实，不伪造平仓时点） */
export interface VrTrade {
  seq: number;
  entry_time: string;
  exit_time: string | null;
  entry_price: number | null;
  exit_price: number | null;
  size: number | null;
  direction: string | null;
  pnl: number | null;
  /** 决策理由（D6 极简形 {"trigger": null}——触发明细不可得如实 null） */
  decision: { trigger: string | null } | null;
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

/** 信号留痕行（VrSignalTrace 读形——outcome 三族 filled/rejected/swallowed；
 *  event_code 色族：10-13 蓝=策略动作/20-25 金=风控出场/30-34 灰=未遂；
 *  reason 五值两族（决策层 exposure_cap/insufficient_funds/
 *  risk_budget_fallback+求值层 entry_exit_conflict/add_inhibited） */
export interface VrTraceRow {
  exec_ref: string;
  bar_time: string;
  seq: number;
  outcome: "filled" | "rejected" | "swallowed";
  reason: string | null;
  event_code: number;
  size: number | null;
  price: number | null;
  /** 截断比例=实际÷计划∈(0,1)；未截断/不可比 null */
  capped: number | null;
  gene_hits: Array<Record<string, unknown>> | null;
}

export interface VrExecution {
  exec_ref: string;
  exec_index: number;
  range_start: string;
  range_end: string;
  trades: VrTrade[];
  equity: VrEquity;
  /** trail 档逐 bar 止损线真值（引擎单源 [{time,value}]；非 trail 档
   *  null；存档早于 trail 能力的记录缺键 null——D2 禁事后重算） */
  trail_series: Array<{ time: string; value: number | null }> | null;
  /** 该份留痕行（seq 升序；无行=[]——四元判读协议见 V3） */
  trace_rows: VrTraceRow[];
}

/** 判定并集余量（union-balance 端点——D1 余量行数据源） */
export interface UnionBalanceResponse {
  hypothesis_id: string;
  symbol: string;
  timeframe: string;
  judged: Array<{ start: string; end: string }>;
  remaining: Array<{ start: string; end: string }>;
  remaining_bars: number;
}

/** detail 两态：detail_available=false → executions=[] 空骨架（历史无留痕如实）；
 *  三布尔 true/false/null 直译（null=产生于信号留痕能力上线前——代际
 *  陈述由前端承载；false=管线执行失败缺陷态） */
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
  signal_trace: boolean | null;
  trade_semantics: boolean | null;
  stop_price: boolean | null;
  /** MTF 门控拦截计数（记录级顶层——null=无门控面与 0=全过严格区分） */
  mtf_gated_bars: number | null;
  executions: VrExecution[];
}
export async function getValidationRecordDetail(
  recordId: string,
): Promise<ValidationDetailResponse> {
  return api.get(`/api/research/validation-records/${recordId}/detail`);
}

/** 判定并集余量查询（三参必填；adhoc 记录 hypothesis_id="adhoc" 查询
 *  404——前端按「余量不可达」降级） */
export async function getUnionBalance(params: {
  symbol: string;
  timeframe: string;
  hypothesis_id: string;
}): Promise<UnionBalanceResponse> {
  return api.get("/api/research/union-balance", { params });
}
