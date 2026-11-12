/**
 * 验证记录语义纯函数层（upgrade-validation-records-list 5.1/5.2）：
 * 卡片四段模型提取（三型 result/params 形状路由）+事件码色族/动作
 * 单源映射+reason 五值两族中文映射。数据零观点——全部键位按后端
 * 实锚直读（validation_service 三型组装/backtest_bridge metrics 形制/
 * trace_resolver EVENT_REASON_MAP），不自算不推断。
 */
import { CHART_VB, ATTENTION, TEXT } from "@/theme/tokens";
import type { ValidationRecordRow, ValidationSort } from "@/services/validationRecords";
import type { VrTraceRow } from "@/services/validationRecords";

/** 列表检索完整形制（六维+排序；宿主 Verify 透传）——类型与缺省值在
 *  本纯函数层（组件文件仅导出组件——react-refresh 约束） */
export interface ListFilters {
  idType: "hypothesis_id" | "strategy_id" | "q";
  id?: string;
  q?: string;
  level?: string;
  verdict?: string;
  generalization?: boolean;
  sort: ValidationSort;
}

export const DEFAULT_LIST_FILTERS: ListFilters = { idType: "q", sort: "time_desc" };

/** 搜索框三路由（L2——前缀路由保留+statement 关键词）：hyp_ 前缀→
 *  hypothesis_id 精确、st_ 前缀→strategy_id 精确（策略 ID 实锚 st_
 *  {hex12}）、其余非空→q 关键词 */
export function parseSearch(text: string): Pick<ListFilters, "idType" | "id" | "q"> {
  const trimmed = text.trim();
  if (!trimmed) return { idType: "q", id: undefined, q: undefined };
  if (trimmed.startsWith("hyp_")) return { idType: "hypothesis_id", id: trimmed };
  if (trimmed.startsWith("st_")) return { idType: "strategy_id", id: trimmed };
  return { idType: "q", q: trimmed };
}

/** 色点单源（三视图同色：K 线标记/逐笔表/信号回溯——蓝策略/金风控
 *  出场族/灰未遂族；trace_resolver 蓝族 10-13/金族 20-25/灰族 30-34） */
export const TRACE_FAMILY_COLOR = {
  strategy: CHART_VB.blue,
  risk: ATTENTION.gold,
  blocked: TEXT.tertiary,
} as const;
export type TraceFamily = keyof typeof TRACE_FAMILY_COLOR;

/** 事件码 → 动作语义+色族+中文标签（四类动作标记▲开多/▲加多/▽减仓/
 *  ▼平多的单源；金族出场细分标签用于信号回溯/逐笔表） */
const EVENT_SEMANTICS: Record<number, {
  action: "open" | "add" | "reduce" | "exit";
  family: TraceFamily;
  markerText: string;
  label: string;
}> = {
  10: { action: "open", family: "strategy", markerText: "▲开多", label: "入场" },
  11: { action: "add", family: "strategy", markerText: "▲加多", label: "加仓" },
  12: { action: "reduce", family: "strategy", markerText: "▽减仓", label: "减仓" },
  13: { action: "exit", family: "strategy", markerText: "▼平多", label: "信号离场" },
  20: { action: "exit", family: "risk", markerText: "▼平多", label: "止损" },
  21: { action: "exit", family: "risk", markerText: "▼平多", label: "止盈" },
  22: { action: "exit", family: "risk", markerText: "▼平多", label: "清算强平" },
  23: { action: "exit", family: "risk", markerText: "▼平多", label: "金额防线" },
  24: { action: "exit", family: "risk", markerText: "▼平多", label: "固定时刻" },
  25: { action: "exit", family: "risk", markerText: "▼平多", label: "max_hold" },
};

export function eventSemantics(code: number) {
  return EVENT_SEMANTICS[code] ?? null;
}

/** reason 五值两族中文映射（决策层三值+求值层两值——rejected/swallowed 行） */
export const REASON_LABELS: Record<string, string> = {
  exposure_cap: "敞口上限拦截",
  insufficient_funds: "资金不足",
  risk_budget_fallback: "风险预算回退",
  entry_exit_conflict: "进出冲突清洗",
  add_inhibited: "加仓抑制清洗",
};

/** outcome 三族中文（filled=触发且成交/rejected=决策层拒绝未成交/
 *  swallowed=求值层清洗未达引擎——吞与拒不混） */
export const OUTCOME_LABELS: Record<VrTraceRow["outcome"], string> = {
  filled: "成交",
  rejected: "被拒",
  swallowed: "吞没",
};

/** K 线四类动作标记（trace_rows filled 行 → 标记形制；标记/逐笔/定位
 *  统一成交锚=bar_time 执行锚）。text=动作名+成交价（price null 历史行
 *  仅动作名不伪造——EVENT_SEMANTICS 唯一权威，其余映射逐位不动） */
export function traceMarkers(rows: VrTraceRow[]): Array<{
  time: string;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
}> {
  return rows
    .filter((r) => r.outcome === "filled")
    .map((r) => {
      const sem = eventSemantics(r.event_code);
      if (!sem) return null;
      return {
        time: r.bar_time,
        position: sem.action === "open" || sem.action === "add"
          ? ("belowBar" as const)
          : ("aboveBar" as const),
        color: TRACE_FAMILY_COLOR[sem.family],
        shape: sem.action === "open" || sem.action === "exit"
          ? (sem.action === "open" ? ("arrowUp" as const) : ("arrowDown" as const))
          : ("circle" as const),
        text: sem.markerText + (r.price != null ? ` ${r.price.toFixed(1)}` : ""),
      };
    })
    .filter((m): m is NonNullable<typeof m> => m !== null);
}

/** 卡片四段模型（L1——行内嵌 result JSON 直读非自算；三型键位路由与
 *  后端排序列 worst 双路径同源：isolated $.metrics / random $
 *  summary.worst_window.metrics / staged summary.worst_segment 五键） */
export interface RecordCardModel {
  typeLabel: string;
  statement: string | null;
  rangeLabel: string;
  symbol: string | null;
  trades: number | null;
  metrics: {
    total_return: number | null;
    sharpe_ratio: number | null;
    max_drawdown: number | null;
  } | null;
  /** worst 槽位身份标签（random=最差窗口/staged=最弱段·label；isolated null） */
  worstLabel: string | null;
}

const LEVEL_LABELS: Record<string, string> = {
  isolated_backtest: "隔离验证",
  random_window: "随机窗口",
  staged_validation: "分段验证",
  composite: "组合验证",
  component: "分量验证",
};

function fmtRange(start?: string | null, end?: string | null): string {
  const s = start?.slice(0, 10);
  const e = end?.slice(0, 10);
  if (!s && !e) return "—";
  return `${s || "?"} → ${e || "?"}`;
}

export function buildCardModel(row: ValidationRecordRow): RecordCardModel {
  const params = (row.params ?? {}) as Record<string, unknown>;
  const result = (row.result ?? {}) as Record<string, unknown>;
  const typeLabel = LEVEL_LABELS[row.level] ?? row.level;
  const base = { typeLabel, statement: row.statement };

  // 记录级区间/币种（三型 params 形状路由——isolated/staged=
  // request_range 四键、random=data_start/data_end+symbol 平铺）
  const rr = params.request_range as
    | { symbol?: string; start?: string; end?: string }
    | undefined;

  let worstLabel: string | null = null;
  let metrics: RecordCardModel["metrics"] = null;
  let trades: number | null = null;

  if (row.level === "isolated_backtest") {
    const m = result.metrics as Record<string, number | null> | undefined;
    if (m && typeof m.total_return === "number") {
      metrics = {
        total_return: m.total_return,
        sharpe_ratio: typeof m.sharpe_ratio === "number" ? m.sharpe_ratio : null,
        max_drawdown: typeof m.max_drawdown === "number" ? m.max_drawdown : null,
      };
      trades = typeof m.total_trades === "number" ? m.total_trades : null;
    }
    return {
      ...base,
      rangeLabel: fmtRange(rr?.start, rr?.end),
      symbol: (result.symbol as string | undefined) ?? rr?.symbol ?? null,
      trades,
      metrics,
      worstLabel,
    };
  }

  if (row.level === "random_window") {
    const summary = result.summary as {
      worst_window?: {
        start?: string;
        metrics?: Record<string, number | null>;
      };
    } | undefined;
    const w = summary?.worst_window;
    const wm = w?.metrics;
    if (wm && typeof wm.total_return === "number") {
      metrics = {
        total_return: wm.total_return,
        sharpe_ratio: typeof wm.sharpe_ratio === "number" ? wm.sharpe_ratio : null,
        max_drawdown: typeof wm.max_drawdown === "number" ? wm.max_drawdown : null,
      };
      trades = typeof wm.total_trades === "number" ? wm.total_trades : null;
      worstLabel = "最差窗口";
    }
    return {
      ...base,
      rangeLabel: fmtRange(
        params.data_start as string | undefined,
        params.data_end as string | undefined,
      ),
      symbol: (params.symbol as string | undefined) ?? null,
      trades,
      metrics,
      worstLabel,
    };
  }

  if (row.level === "staged_validation") {
    const summary = result.summary as {
      worst_segment?: Record<string, number | string | null>;
    } | undefined;
    const w = summary?.worst_segment;
    // worst_segment 五键（label/total_return/sharpe_ratio/max_drawdown/
    // total_trades——B1.5 补平前存量 sharpe/max_drawdown 容缺 null）
    if (w && typeof w.total_return === "number") {
      metrics = {
        total_return: w.total_return,
        sharpe_ratio: typeof w.sharpe_ratio === "number" ? w.sharpe_ratio : null,
        max_drawdown: typeof w.max_drawdown === "number" ? w.max_drawdown : null,
      };
      trades = typeof w.total_trades === "number" ? w.total_trades : null;
      worstLabel = `最弱段·${String(w.label ?? "?")}`;
    }
    return {
      ...base,
      rangeLabel: fmtRange(rr?.start, rr?.end),
      symbol: rr?.symbol ?? null,
      trades,
      metrics,
      worstLabel,
    };
  }

  // composite/component（死值域——无 worst 槽位，指标位「无指标留痕」）
  return {
    ...base,
    rangeLabel: fmtRange(rr?.start, rr?.end),
    symbol: rr?.symbol ?? null,
    trades,
    metrics,
    worstLabel,
  };
}

/** 选中行三型 symbol/timeframe 提取（K 线复核与 union-balance 入参） */
export function recordSymbolTimeframe(row: ValidationRecordRow): {
  symbol: string | null;
  timeframe: string | null;
} {
  const params = (row.params ?? {}) as Record<string, unknown>;
  const result = (row.result ?? {}) as Record<string, unknown>;
  const rr = params.request_range as
    | { symbol?: string; timeframe?: string }
    | undefined;
  if (row.level === "isolated_backtest") {
    return {
      symbol: (result.symbol as string | undefined) ?? rr?.symbol ?? null,
      timeframe: (result.timeframe as string | undefined)
        ?? rr?.timeframe ?? null,
    };
  }
  if (row.level === "random_window") {
    return {
      symbol: (params.symbol as string | undefined) ?? null,
      timeframe: (params.timeframe as string | undefined) ?? null,
    };
  }
  return {
    symbol: rr?.symbol ?? (result.request_range as
      | { symbol?: string }
      | undefined)?.symbol ?? null,
    timeframe: rr?.timeframe ?? (result.request_range as
      | { timeframe?: string }
      | undefined)?.timeframe ?? null,
  };
}
