/**
 * 验证记录语义纯函数层（upgrade-validation-records-list 5.1/5.2）：
 * 卡片四段模型提取（三型 result/params 形状路由）+事件码色族/动作
 * 单源映射+reason 五值两族中文映射。数据零观点——全部键位按后端
 * 实锚直读（validation_service 三型组装/backtest_bridge metrics 形制/
 * trace_resolver EVENT_REASON_MAP），不自算不推断。
 */
import { CHART_VB, ATTENTION, TEXT } from "@/theme/tokens";
import { toTime } from "@/components/charts/core/time";
import { formatTimestamp } from "@/lib/utils";
import type { ValidationRecordRow, ValidationSort } from "@/services/validationRecords";
import type { VrExecution, VrTrade, VrTraceRow } from "@/services/validationRecords";

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

/** 字义模板（fe-vr-marker-fidelity 契约 2）：方向代入生成 marker 字义。
 *  directional 类（open/exit）long/short 两态不同；非向类（add/reduce）
 *  两态同值（方向不代入）。 */
export interface MarkerMorph {
  long: string;
  short: string;
}

/** marker 箭头形制（契约 2，A3 方向参数化）。映射语义基准=买卖语义
 *  （买入向上/卖出向下）：开多/平空 arrowUp、开空/平多 arrowDown——
 *  与 charts/primitives/markers.ts SIGNAL_STYLE 先例逐条一致。 */
export type MarkerShape = "arrowUp" | "arrowDown" | "circle";

/** 事件词条（契约 2 终态形）：action/family/label 三字段冻结（D5：
 *  TradeTable 锚点列/SignalTrace 动作列依赖，语义零变化）；morph/
 *  arrowLong/arrowShort 为方向代入面（新增）。 */
export interface SemanticsEntry {
  action: "open" | "add" | "reduce" | "exit";
  family: TraceFamily;
  label: string;
  morph: MarkerMorph;
  arrowLong: MarkerShape;
  arrowShort: MarkerShape;
}

/** 事件码 → 动作语义+色族+中文标签（四类动作标记的单源；金族出场
 *  细分标签用于信号回溯/逐笔表）。fe-vr-marker-fidelity 契约 2 终态：
 *  morph/arrowLong/arrowShort 方向代入面（六字义=开多/开空/加仓/
 *  减仓/平多/平空）。词表覆盖域=10-25 的 filled 面，35/36 无词条
 *  维持现状（Q1 基线不收编——实施不得顺手补词条）。 */
const EVENT_SEMANTICS: Record<number, SemanticsEntry> = {
  10: { action: "open", family: "strategy", label: "入场",
    morph: { long: "开多", short: "开空" }, arrowLong: "arrowUp", arrowShort: "arrowDown" },
  11: { action: "add", family: "strategy", label: "加仓",
    morph: { long: "加仓", short: "加仓" }, arrowLong: "circle", arrowShort: "circle" },
  12: { action: "reduce", family: "strategy", label: "减仓",
    morph: { long: "减仓", short: "减仓" }, arrowLong: "circle", arrowShort: "circle" },
  13: { action: "exit", family: "strategy", label: "信号离场",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
  20: { action: "exit", family: "risk", label: "止损",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
  21: { action: "exit", family: "risk", label: "止盈",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
  22: { action: "exit", family: "risk", label: "清算强平",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
  23: { action: "exit", family: "risk", label: "金额防线",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
  24: { action: "exit", family: "risk", label: "固定时刻",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
  25: { action: "exit", family: "risk", label: "max_hold",
    morph: { long: "平多", short: "平空" }, arrowLong: "arrowDown", arrowShort: "arrowUp" },
};;

export function eventSemantics(code: number) {
  return EVENT_SEMANTICS[code] ?? null;
}

/* ══ fe-vr-marker-fidelity 纯函数层：方向归一（契约 0）/方向解析回退链
    （契约 1）/字义代入（契约 2）/侧位口径（契约 4）══ */

/** 方向原始值归一（契约 0）：双形制白名单收敛到行级方向二值。
 *  白名单恰四形："long"|"Long" → "long"；"short"|"Short" → "short"；
 *  其余一切（含 "mixed"、"LONG"/"Short " 等任何非白名单形、
 *  undefined、null）→ null。非 toLowerCase 语义——全大写等变体不在
 *  白名单即 null（v2.2 定谳：白名单与两权威源值域精确对齐——记录级
 *  result.direction 小写 long/short/mixed、逐笔 VrTrade.direction
 *  首字母大写 Long/Short——命中非白名单形说明数据异常，按缺失走
 *  回退链而非猜成合法方向，与 D6「不猜」一致）。 */
export function normalizeDirection(raw: string | null | undefined): "long" | "short" | null {
  if (raw === "long" || raw === "Long") return "long";
  if (raw === "short" || raw === "Short") return "short";
  return null;
}

/** 入场/出场事件码集（契约 1 行匹配分派：入场类 {10,11} 查入场桶、
 *  出场类 {12,13,20-25} 查出场桶——含 reduce(12)，出场锚=exit_time） */
const ENTRY_EVENT_CODES = new Set([10, 11]);
const EXIT_EVENT_CODES = new Set([12, 13, 20, 21, 22, 23, 24, 25]);

/** 份级方向查表闭包工厂（契约 1）：对给定执行份产出「trace 行 →
 *  行级方向」的查表函数。四层分派（架构 D7）：①recordDirection 收敛
 *  为 long/short → 份级常量（零 join，纯记录级判定）；②mixed/缺失
 *  （含脏值）→ 行级分钟键桶序配对（对齐语义复刻后端 resolver _match
 *  的 consumed 顺序消费）；③桶不中/桶耗尽/trades 空 → 该行 null；
 *  ④null 即「不猜维持旧字义」（D6，由字义生成层消费）。
 *  函数性（两分句）：工厂纯——同参两次调用产出两个独立闭包、各自桶
 *  游标从零；返回闭包是行级迭代查表（游标随行匹配推进是配对算法
 *  本质），按「每行恰调用一次」契约使用——同一闭包对同一 row 重复
 *  调用的行为不在契约内（不测、不依赖）。 */
export function resolveDirectionLookup(
  recordDirection: string | undefined,
  exec: VrExecution,
): (row: VrTraceRow) => "long" | "short" | null {
  const constant = normalizeDirection(recordDirection);
  if (constant !== null) return () => constant;

  // 建桶：分钟键（slice(0,16)——与 TradeTable exitAnchors 先例同粒度）
  // 分桶，桶内 seq 升序（乱序输入先稳定排序再入桶）；出场桶仅收
  // exit_time 非 null 的笔
  const entryBuckets = new Map<string, VrTrade[]>();
  const exitBuckets = new Map<string, VrTrade[]>();
  for (const t of [...exec.trades].sort((a, b) => a.seq - b.seq)) {
    const entryKey = t.entry_time.slice(0, 16);
    const entryList = entryBuckets.get(entryKey);
    if (entryList) entryList.push(t);
    else entryBuckets.set(entryKey, [t]);
    if (t.exit_time != null) {
      const exitKey = t.exit_time.slice(0, 16);
      const exitList = exitBuckets.get(exitKey);
      if (exitList) exitList.push(t);
      else exitBuckets.set(exitKey, [t]);
    }
  }
  // 游标：每桶已消费计数（复刻 _match 桶内序号顺序消费——同桶多笔按
  // seq 升序逐行分配；entry/exit 键空间独立各自计数）
  const entryCursors = new Map<string, number>();
  const exitCursors = new Map<string, number>();
  return (row: VrTraceRow): "long" | "short" | null => {
    if (row.outcome !== "filled") return null;
    const minuteKey = row.bar_time.slice(0, 16);
    let bucket: VrTrade[] | undefined;
    let cursors: Map<string, number>;
    if (ENTRY_EVENT_CODES.has(row.event_code)) {
      bucket = entryBuckets.get(minuteKey);
      cursors = entryCursors;
    } else if (EXIT_EVENT_CODES.has(row.event_code)) {
      bucket = exitBuckets.get(minuteKey);
      cursors = exitCursors;
    } else {
      return null;
    }
    if (!bucket) return null;
    const cursor = cursors.get(minuteKey) ?? 0;
    if (cursor >= bucket.length) return null;
    cursors.set(minuteKey, cursor + 1);
    return normalizeDirection(bucket[cursor].direction);
  };
}

/** 字义生成（契约 2）：方向代入 morph。direction=null 维持旧字义
 *  （D6 不猜——open→"开多"、exit→"平多"，已知失真如实维持）；
 *  add/reduce 与方向无关（两态同值）。 */
export function markerMorphText(
  entry: SemanticsEntry,
  direction: "long" | "short" | null,
): string {
  return direction === "short" ? entry.morph.short : entry.morph.long;
}

/** 箭头形制（契约 2）：方向代入 arrow 面。null 取 arrowLong 列——与
 *  现状 traceMarkers 的 shape 规则逐位一致（open→arrowUp/exit→
 *  arrowDown/add・reduce→circle），D6「维持旧字义」在箭头面的含义=
 *  现状 shape 行为保持。 */
export function markerShape(
  entry: SemanticsEntry,
  direction: "long" | "short" | null,
): MarkerShape {
  return direction === "short" ? entry.arrowShort : entry.arrowLong;
}

/** 动态就近侧位口径（契约 4，R2 冻结）：成交价与所在 bar 中点比较，
 *  ≥(high+low)/2 → aboveBar（标上），＜中点 → belowBar（标下）。
 *  价在 bar 外时该口径自然得出就近侧（价>high 必≥中点→上）。判定
 *  公式单源（D3 口径只写一次），组装层基准/吸附两视图同一函数两用。 */
export function markerSide(
  price: number,
  bar: { high: number; low: number },
): "aboveBar" | "belowBar" {
  return price >= (bar.high + bar.low) / 2 ? "aboveBar" : "belowBar";
}

/** 无价行/无 bar 行的旧惯例侧位（契约 4，R4 冻结）：入场族（open/
 *  add）下方、出场族（reduce/exit）上方——即现状 traceMarkers 的
 *  position 规则显式化收编（E 无价回退沿用）。 */
export function legacySide(action: "open" | "add" | "reduce" | "exit"): "aboveBar" | "belowBar" {
  return action === "open" || action === "add" ? "belowBar" : "aboveBar";
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

/** marker 草稿（契约 3）：rows→draft 纯函数产出——侧位不在本层
 *  （position 依赖「吸附后 bar」，属组装层信息 D3，由 markerSide/
 *  legacySide 在组装层判定）；price 非 null=锚定通道依据与锚定价位。 */
export interface TraceMarkerDraft {
  time: string;
  color: string;
  shape: MarkerShape;
  text: string;
  price: number | null;
  action: "open" | "add" | "reduce" | "exit";
}

/** K 线四类动作标记（trace_rows filled 行 → draft；标记/逐笔/定位
 *  统一成交锚=bar_time 执行锚）。fe-vr-marker-fidelity 契约 3 终态：
 *  filter filled（不变）→ 逐行查词条（无词条滤除不变，35/36 现状）
 *  → 方向代入生成 morph 字义+箭头（P2 去符号——text=六字义纯文字+
 *  可选价格后缀 toFixed(1) 现状形制；direction=null 维持旧字义 D6）
 *  → price/action 透传（侧位判定与锚定通道输入）。direction 参数=
 *  查表闭包（契约 1 产出）注入式保纯函数可单测；闭包按「每行恰调用
 *  一次」契约使用——无词条行不调用（不消费桶）。 */
export function traceMarkers(
  rows: VrTraceRow[],
  direction: (row: VrTraceRow) => "long" | "short" | null,
): TraceMarkerDraft[] {
  return rows
    .filter((r) => r.outcome === "filled")
    .map((r): TraceMarkerDraft | null => {
      const sem = eventSemantics(r.event_code);
      if (!sem) return null;
      const dir = direction(r);
      return {
        time: r.bar_time,
        color: TRACE_FAMILY_COLOR[sem.family],
        shape: markerShape(sem, dir),
        text: markerMorphText(sem, dir) + (r.price != null ? ` ${r.price.toFixed(1)}` : ""),
        price: r.price,
        action: sem.action,
      };
    })
    .filter((m): m is TraceMarkerDraft => m !== null);
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
  sandbox_backtest: "沙盒回测",
  replay: "回放",
};

function fmtRange(start?: string | null, end?: string | null): string {
  const s = start != null ? formatTimestamp(start, "date") : undefined;
  const e = end != null ? formatTimestamp(end, "date") : undefined;
  if (!s && !e) return "—";
  return `${s || "?"} → ${e || "?"}`;
}

export function buildCardModel(row: ValidationRecordRow): RecordCardModel {
  const params = (row.params ?? {}) as Record<string, unknown>;
  const result = (row.result ?? {}) as Record<string, unknown>;
  const typeLabel = LEVEL_LABELS[row.level] ?? row.level;
  // statement 生成物为可选键（Pydantic 默认值字段不进 required——运行时
  // 恒在场，D-1 探针双证）；边界处 undefined 归一为 null（视图模型必带键）
  const base = { typeLabel, statement: row.statement ?? null };

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

  if (row.level === "sandbox_backtest" || row.level === "replay") {
    // 沙盒/回放收编（fix-replay-list-channel FE-2/FE-3）：params 顶层
    // 平铺形制（两档同一 save_sandbox_validation_record 写路径同构——
    // symbol/timeframe/start/end 顶层+费滑杠杆）；指标槽按档取值：
    // sandbox=result.metrics 五项真值、replay=result.replay 真值
    // （total_return 计算键——历史行缺键时指标组如实缺省，呈现面
    // 不做 PnL 代理；笔数两档均取 total_trades）
    let m2: RecordCardModel["metrics"] = null;
    let trades2: number | null = null;
    if (row.level === "sandbox_backtest") {
      const m = result.metrics as
        Record<string, number | null> | undefined;
      if (m && typeof m.total_return === "number") {
        m2 = {
          total_return: m.total_return,
          sharpe_ratio: typeof m.sharpe_ratio === "number" ? m.sharpe_ratio : null,
          max_drawdown: typeof m.max_drawdown === "number" ? m.max_drawdown : null,
        };
        trades2 = typeof m.total_trades === "number" ? m.total_trades : null;
      }
    } else {
      const rp = result.replay as
        Record<string, number | string | null> | undefined;
      if (rp && typeof rp.total_return === "number") {
        m2 = {
          total_return: rp.total_return,
          sharpe_ratio: null,
          max_drawdown: null,
        };
      }
      trades2 = rp && typeof rp.total_trades === "number"
        ? rp.total_trades : null;
    }
    return {
      ...base,
      rangeLabel: fmtRange(params.start as string | undefined,
                           params.end as string | undefined),
      symbol: (params.symbol as string | undefined) ?? null,
      trades: trades2,
      metrics: m2,
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

/** 库值 symbol/timeframe 白名单（字符级值域闸）：
 *  实存形态 [A-Z0-9]+（代码与契约快照字面全集）；含 '-' 输入形态由后端
 *  safe_symbol 契约佐证（"BTC-USDT" 与 "BTCUSDT" 同文件）；'_' 为文件名
 *  拼接符上界容差；结构性排除 '.' 与 '/'——encodeURIComponent 对 '.' 恒等、
 *  整段 '..' 会被 new URL 规范化重组致路径偏移，含点形态一律闸下。 */
const SYMBOL_TIMEFRAME_RE = /^[A-Za-z0-9_-]{1,32}$/;

/** 值域闸：库值过白名单放行原值，否则 null（与既有 ?? null 链的缺失语义同构） */
function gateLibValue(v: string | null): string | null {
  return v !== null && SYMBOL_TIMEFRAME_RE.test(v) ? v : null;
}

/** 选中行三型 symbol/timeframe 原值提取（私有：三型差异原样保留，
 *  出口统一由 recordSymbolTimeframe 过闸） */
function extractRawSymbolTimeframe(row: ValidationRecordRow): {
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
    // 形制兼容根治（fe-replay-presentation 任务 1.1）：params 顶层
    // symbol/timeframe（引擎落库新形制——level=replay/sandbox 两用）
    // 与 params.request_range 内嵌（旧形制）统一识别，顶层优先。
    symbol: (params.symbol as string | undefined)
      ?? rr?.symbol ?? (result.request_range as
      | { symbol?: string }
      | undefined)?.symbol ?? null,
    timeframe: (params.timeframe as string | undefined)
      ?? rr?.timeframe ?? (result.request_range as
      | { timeframe?: string }
      | undefined)?.timeframe ?? null,
  };
}

/** 选中行三型 symbol/timeframe 提取（K 线复核与 union-balance 入参）。
 *  库值值域闸（validation-records-page「库值 symbol/timeframe 值域闸」
 *  Requirement）：三型提取结果统一出口过闸，不匹配置 null（缺失语义，
 *  与 ?? null 链同构）、两字段独立判定——污染库值不透传、不报错。 */
export function recordSymbolTimeframe(row: ValidationRecordRow): {
  symbol: string | null;
  timeframe: string | null;
} {
  const raw = extractRawSymbolTimeframe(row);
  // 出口统一过闸：三型提取结果同一条白名单，闸门与提取同址
  return { symbol: gateLibValue(raw.symbol), timeframe: gateLibValue(raw.timeframe) };
}

/** ══ 契约 H/L：DNA 指标提取与降级文案（upgrade-vr-indicator-overlay）══ */

/** 降级文案常量（契约 L——可断言契约串，组件/e2e 引用同源） */
export const OVERLAY_UNSUPPORTED_TEXT = "该指标暂不支持叠加";
export const OVERLAY_SUBCHART_TEXT = "副图已用于资金曲线，暂不叠加";
export const OVERLAY_MTF_TEXT = "跨周期层指标暂不叠加";
export const LAYER_NOTE = "跨周期层";

export interface DnaOverlayEma {
  kind: "EMA";
  period: number;
}
export interface DnaOverlayBb {
  kind: "BB";
  period: number;
  std: number;
  /** 基因引用的输出轨（middle 等——端点固定返回三带完整线，field 仅供徽标披露） */
  field?: string;
}
export type DnaOverlay = DnaOverlayEma | DnaOverlayBb;

/** DNA 指标三分判别结果（overlayable=主图可叠加/subChartOnly=副图族降级/
 *  unsupported=注册表其余键+跨周期层——裁决亮出非静默丢弃） */
export interface DnaIndicatorExtract {
  overlayable: DnaOverlay[];
  subChartOnly: Array<{ kind: "RSI" | "MACD" | "KDJ"; raw: unknown }>;
  unsupported: Array<{ indicator: string; note?: string }>;
}

interface GeneLike {
  indicator?: unknown;
  params?: unknown;
  field?: unknown;
}

/**
 * Strategy["dna"] → 指标三分判别（契约 H）。判定表机械化：
 * EMA→overlayable（params.period 直映射 ema_periods）；BB→overlayable
 * （period/std 同名直映射——实测锚 st_8fac17534a47 {period:20,std:2}
 * field=middle）；RSI/MACD/KDJ→subChartOnly；注册表其余键→unsupported；
 * layers 各层 signal_genes 全族→unsupported（跨周期层）。多基因聚合：
 * 多 EMA periods 去重、多 BB 参数组去重、同参数重复基因去重；形态异常
 * 归 unsupported 不报错；dna 缺失/genes 空→三分全空。
 */
export function dnaIndicators(dna: unknown): DnaIndicatorExtract {
  const out: DnaIndicatorExtract = { overlayable: [], subChartOnly: [], unsupported: [] };
  if (!dna || typeof dna !== "object") return out;
  const genes = (dna as { signal_genes?: unknown }).signal_genes;
  if (Array.isArray(genes)) {
    const seenEma = new Set<number>();
    const seenBb = new Set<string>();
    const seenSub = new Set<string>();
    for (const g of genes as GeneLike[]) {
      const ind = g?.indicator;
      const params = (g?.params ?? {}) as Record<string, unknown>;
      if (ind === "EMA" && typeof params.period === "number") {
        if (!seenEma.has(params.period)) {
          seenEma.add(params.period);
          out.overlayable.push({ kind: "EMA", period: params.period });
        }
      } else if (
        ind === "BB" &&
        typeof params.period === "number" &&
        typeof params.std === "number"
      ) {
        const key = `${params.period}:${params.std}`;
        if (!seenBb.has(key)) {
          seenBb.add(key);
          out.overlayable.push({
            kind: "BB",
            period: params.period,
            std: params.std,
            ...(typeof g.field === "string" ? { field: g.field } : {}),
          });
        }
      } else if (ind === "RSI" || ind === "MACD" || ind === "KDJ") {
        if (!seenSub.has(ind)) {
          seenSub.add(ind);
          out.subChartOnly.push({ kind: ind, raw: g });
        }
      } else {
        out.unsupported.push({ indicator: String(ind ?? "?") });
      }
    }
  }
  const layers = (dna as { layers?: unknown }).layers;
  if (Array.isArray(layers)) {
    for (const layer of layers as Array<{ signal_genes?: unknown }>) {
      const lg = layer?.signal_genes;
      if (!Array.isArray(lg)) continue;
      for (const g of lg as GeneLike[]) {
        out.unsupported.push({
          indicator: String(g?.indicator ?? "?"),
          note: OVERLAY_MTF_TEXT,
        });
      }
    }
  }
  return out;
}

/** ══ 契约 K：时间点吸附（周期切换查看粒度——执行语义锚恒为基准周期）══ */

export interface SnapResult {
  /** 逐点吸附结果（保留输入序；超域点不出现） */
  snapped: Array<{ from: string; to: string }>;
  /** 超域（早于首 bar 或 >= 尾 bar+tf）丢弃计数 */
  dropped: number;
}

/**
 * 基准周期时间点吸附到目标周期 bars（契约 K）：t 落在 bar 的
 * [bar.time, bar.time+tf) 左闭右开区间→挂该 bar（t==bar.time 恰开盘挂
 * 该 bar；t==下一根开盘挂下一根）。实现=有序 bars 二分查找；超域点丢弃
 * 并计数披露。bars 须 epoch 升序（与装载输出去重序一致）。
 */
export function snapToBars(
  times: string[],
  bars: Array<{ timestamp: string }>,
  tfSec: number,
): SnapResult {
  const epochOf = (ts: string): number | null => {
    const t = toTime(ts);
    return typeof t === "number" ? t : null;
  };
  // 同域索引（epoch 与 timestamp 成对）：malformed bar 不进吸附域（现状
  // 同），二分与回填同取本域——barEpochs 单独压栈时跳过 malformed 会使
  // 索引与 bars 错位，回填 bars[idx] 串到错误的 bar（CARD-03）
  const barDomain: Array<{ e: number; ts: string }> = [];
  for (const b of bars) {
    const e = epochOf(b.timestamp);
    if (e != null) barDomain.push({ e, ts: b.timestamp });
  }
  if (barDomain.length === 0 || tfSec <= 0) return { snapped: [], dropped: times.length };
  const snapped: SnapResult["snapped"] = [];
  let dropped = 0;
  for (const t of times) {
    const te = epochOf(t);
    if (te == null) {
      dropped++;
      continue;
    }
    // 二分：找最后一根 bar.time <= te 的 bar（左闭右开归属）
    let lo = 0;
    let hi = barDomain.length - 1;
    let idx = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (barDomain[mid].e <= te) {
        idx = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    if (idx < 0 || te >= barDomain[idx].e + tfSec) {
      // 早于首 bar 或超出尾 bar 覆盖域
      dropped++;
      continue;
    }
    snapped.push({ from: t, to: barDomain[idx].ts });
  }
  return { snapped, dropped };
}
