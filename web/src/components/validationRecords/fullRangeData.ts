/**
 * 全区间 K 线装载与 equity 拼接（upgrade-vr-fullrange-chart 契约 A/C）。
 *
 * 服务对象：RecordDetail D2 K 线复核（重构后全区间单图视图）。
 * 职责边界：
 * - useFullRangeKline（契约 A）：min(range_start)~max(range_end) 单次拉取
 *   （显式 limit 50000）+ 按份对账（每执行份闭端 bar 数==该份
 *   equity.values.length，三型统一）+ 四态判别 error→truncated→mismatch→ok
 *   （判定序保证互斥）+ 时间戳去重不变量（合并输出每时间戳恰一根 bar——
 *   呈现防线，与对账解耦）。
 * - buildEquitySeries（契约 C）：equity 锚到真实 bar 时间戳（zip 主路径，
 *   对账通过前提）+ 份级降级锚（留痕自锚：timestamps 直用/compact 等距
 *   重建——网格同构守卫保证 compact 真等距）+ 段界 whitespace 断点。
 * - perExecEquityPoints：单份序列（与副图同源同锚），供单份视图与
 *   V1 权益明细页签消费。
 * - barsInExecRange：份区间闭端过滤单源（对账计数/equity 锚序列/单份
 *   视图主图三处共用——契约 E 渲染层过滤的取数谓词）。
 */
import { useQuery } from "@tanstack/react-query";
import { getChartIndicators, getOhlcvBySymbol } from "@/services/datasets";
import { toTime } from "@/components/charts/core/time";
import type { VrExecution } from "@/services/validationRecords";
import type { DnaOverlay } from "./traceSemantics";

/** K 线 bar（与 KlineChart CandleData 结构同形） */
export interface FullRangeBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** 周期字符串 → 秒数（compact 时间轴重建；全档覆盖数据目录档位） */
export function timeframeSeconds(tf: string): number {
  const m: Record<string, number> = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "3d": 259200,
  };
  return m[tf] ?? 0;
}

/** 字符串时间 → epoch 秒（经 toTime 单源；malformed 回退 null） */
export function epochOf(ts: string): number | null {
  const t = toTime(ts);
  return typeof t === "number" ? t : null;
}

/** 份区间闭端过滤：epoch 闭端 [range_start, range_end] 内的 bar 子集
 *  （malformed 边界返回空——任一端不可解析即无法界定区间）。对账计数、
 *  equity 锚序列、单份视图主图三处共用的单一实现（契约 E） */
export function barsInExecRange(
  bars: FullRangeBar[],
  exec: VrExecution,
): FullRangeBar[] {
  const s = epochOf(exec.range_start);
  const t = epochOf(exec.range_end);
  if (s == null || t == null) return [];
  return bars.filter((b) => {
    const be = epochOf(b.timestamp);
    return be != null && be >= s && be <= t;
  });
}

/** ── 契约 A：四态判别联合 ── */

export interface MismatchedExec {
  exec_ref: string;
  bars: number;
  points: number;
}

export type FullRangeResult =
  | { state: "loading" }
  | { state: "error"; /** REQ-4 判死分流的类型判据源（ApiError.status 可达） */ error: unknown }
  | {
      state: "ok" | "truncated" | "mismatch";
      /** 去重合并后的全区间 bar 序（truncated/mismatch 态照常返回照画） */
      candles: FullRangeBar[];
      /** truncated 态：尾部缺失根数估算（周期粒度） */
      truncatedMissing: number | null;
      /** mismatch 态：不等份明细（份级定位） */
      mismatchedExecs: MismatchedExec[];
      /** 按份对账逐份结果（exec_ref → 对齐？——契约 C zip 资格） */
      execAligned: Record<string, boolean>;
    };

/** 拉取上限（后端 head 截取防线——B2） */
const FETCH_LIMIT = 50000;

export function useFullRangeKline(
  executions: VrExecution[],
  symbol: string | null,
  timeframe: string | null,
  /** 视图周期（契约 K：缺省=基准 timeframe；切换后 K 线按该档重拉） */
  viewTf?: string | null,
  /** REQ-4 重试序号：非 404 失败档重切时递增（queryKey 变化强制重发） */
  retrySeq = 0,
): FullRangeResult {
  const fetchTf = viewTf ?? timeframe;
  const bounds = executions.length
    ? {
        minStart: executions.reduce(
          (acc, e) => (epochOf(e.range_start) != null && (acc == null || e.range_start < acc) ? e.range_start : acc),
          null as string | null,
        ),
        maxEnd: executions.reduce(
          (acc, e) => (epochOf(e.range_end) != null && (acc == null || e.range_end > acc) ? e.range_end : acc),
          null as string | null,
        ),
      }
    : { minStart: null, maxEnd: null };

  const q = useQuery({
    queryKey: ["vr-kline-full", symbol, fetchTf, bounds.minStart, bounds.maxEnd, retrySeq],
    queryFn: () =>
      getOhlcvBySymbol(symbol!, fetchTf!, {
        start: bounds.minStart ?? undefined,
        end: bounds.maxEnd ?? undefined,
        limit: FETCH_LIMIT,
      }),
    enabled: !!symbol && !!fetchTf && !!bounds.minStart && !!bounds.maxEnd,
  });

  if (q.isError) return { state: "error", error: q.error };
  if (q.isLoading || !q.data) return { state: "loading" };

  // 去重不变量：Map by timestamp 保序去重 + epoch 升序（呈现防线）
  const byTs = new Map<string, FullRangeBar>();
  for (const b of (q.data.data ?? []) as FullRangeBar[]) byTs.set(b.timestamp, b);
  const candles = [...byTs.values()].sort(
    (a, b) => (epochOf(a.timestamp) ?? 0) - (epochOf(b.timestamp) ?? 0),
  );

  // 判定序 error→truncated→mismatch→ok（先判先赢）
  const endEpoch = bounds.maxEnd ? epochOf(bounds.maxEnd) : null;
  const lastEpoch = candles.length ? epochOf(candles[candles.length - 1].timestamp) : null;
  const tfSec = fetchTf ? timeframeSeconds(fetchTf) : 0;
  if (endEpoch != null) {
    // truncated=尾 bar 覆盖域判法（两粒度通用）：末根 bar 覆盖
    // [lastEpoch, lastEpoch+tfSec)，range_end 落入覆盖窗（或已达端点）
    // 即完整——非基准档 range_end 落末根粗档 bar 覆盖窗内不再误报；基准
    // 档与 lastEpoch<endEpoch 直比逐位等价（含 tfSec=0 表外周期退化档）
    const coversEnd =
      lastEpoch != null && (lastEpoch >= endEpoch || lastEpoch + tfSec > endEpoch);
    if (!coversEnd) {
      const missing = lastEpoch != null && tfSec > 0
        ? Math.max(1, Math.round((endEpoch - lastEpoch) / tfSec))
        : null;
      return { state: "truncated", candles, truncatedMissing: missing, mismatchedExecs: [], execAligned: {} };
    }
  }

  // 按份对账（三型统一：每执行份闭端 bar 数==该份 equity.values.length）
  // ——等式语义锚=基准粒度：闭端 bar 数只在基准档与 equity 点数可对齐，
  // 非基准档跳过对账（切档不产生假 mismatch——契约 K）
  const mismatchedExecs: MismatchedExec[] = [];
  const execAligned: Record<string, boolean> = {};
  if (fetchTf === timeframe) {
    for (const e of executions) {
      const bars = barsInExecRange(candles, e).length;
      const aligned = bars === e.equity.values.length;
      execAligned[e.exec_ref] = aligned;
      if (!aligned) mismatchedExecs.push({ exec_ref: e.exec_ref, bars, points: e.equity.values.length });
    }
  }
  if (mismatchedExecs.length > 0) {
    return { state: "mismatch", candles, truncatedMissing: null, mismatchedExecs, execAligned };
  }
  return { state: "ok", candles, truncatedMissing: null, mismatchedExecs: [], execAligned };
}

/** ── 契约 C：equity 序列构建 ── */

/**
 * 段界拼接原语（fe-recorddetail-td-deadtfs 组 1 REQ-3a）：已压好点的段
 * 序列拼接+段界 whitespace 断点+段界同 bar 合并。断点取下段首点
 * epoch−1 秒（严格落在两段之间——库要求数据时间严格递增，与下段首值
 * 同时间戳的 whitespace 会构成重复时间项）；brk>lastEpoch 守卫防逆序；
 * 相邻两段首尾同一时间戳（explicit 相接拓扑）时 out 末项替换为下段首项
 * （段界重置在该 bar 内不可表示，合并取后值——与段内同 bar 保尾值规则
 * 统一）。逻辑逐字承自原四函数（buildEquitySeries/snapSeriesParts/
 * snapTrailParts/concatTrail——后者已薄包装接本原语）。
 *
 * 类型参数：TSeg=段元素形态；TOut=输出项形态（equity 族断点为
 * {time} whitespace、trail 族断点为 {time,value:null} null 值点）。
 */
export function concatSegmentsWithBreaks<TSeg, TOut>(
  segments: TSeg[][],
  opts: {
    /** 段首元素的时间串（断点 epoch 与段界合并的判定基准） */
    firstTime: (seg: TSeg[]) => string;
    /** 输出项的时间串 */
    itemTime: (item: TOut) => string;
    /** 断点项构造（equity 族 whitespace / trail 族 null 值点） */
    makeBreak: (epochSec: number) => TOut;
    /** 段 → 输出项序列映射（含 null 归一等段内形制转换） */
    toItems: (seg: TSeg[]) => TOut[];
  },
): TOut[] {
  const out: TOut[] = [];
  let lastEpoch: number | null = null;
  for (const seg of segments) {
    if (seg.length === 0) continue;
    const items = opts.toItems(seg);
    if (items.length === 0) continue;
    const firstEpoch = epochOf(opts.firstTime(seg));
    if (out.length > 0 && firstEpoch != null) {
      const brk = firstEpoch - 1;
      if (lastEpoch != null && brk > lastEpoch) out.push(opts.makeBreak(brk));
    }
    let start = 0;
    if (out.length > 0 && opts.itemTime(out[out.length - 1]) === opts.firstTime(seg)) {
      out[out.length - 1] = items[0];
      start = 1;
    }
    for (let i = start; i < items.length; i++) {
      const item = items[i];
      out.push(item);
      const pe = epochOf(opts.itemTime(item));
      if (pe != null) lastEpoch = pe;
    }
  }
  return out;
}

/** whitespace 断点项（无 value——lightweight-charts 官方断线机制） */
export type EquityPoint = { time: string; value: number } | { time: string };

/** 单份锚序列：zip 主路径（对账通过）或留痕自锚降级（timestamps 直用/
 *  compact 等距重建——原 equityPoints 逻辑内化，时间产出统一 ISO 带 Z） */
function execAnchoredPoints(exec: VrExecution, bars: FullRangeBar[]): Array<{ time: string; value: number }> {
  const values = exec.equity.values;
  if (values.length === 0) return [];
  const inRange = barsInExecRange(bars, exec).map((b) => b.timestamp);
  if (inRange.length === values.length) {
    return values.map((v, i) => ({ time: inRange[i], value: v }));
  }
  // 降级锚一：timestamps 直用
  const eq = exec.equity;
  if (eq.timestamps && eq.timestamps.length === values.length) {
    return values.map((v, i) => ({ time: isoZ(eq.timestamps![i]), value: v }));
  }
  // 降级锚二：compact 等距重建（网格同构守卫保证真等距）
  const sec = timeframeSeconds(eq.timeframe);
  const startEpoch = eq.start ? epochOf(eq.start) : null;
  if (sec > 0 && startEpoch != null) {
    return values.map((v, i) => ({ time: new Date((startEpoch + i * sec) * 1000).toISOString(), value: v }));
  }
  // 终极兜底（现 equityPoints 同形——无 start 无 tf 的退化留痕）
  return values.map((v, i) => ({ time: String(i), value: v }));
}

/** 时间串 → ISO 带 Z（经 toTime 单源解析后格式化；malformed 原样返回） */
function isoZ(ts: string): string {
  const t = toTime(ts);
  return typeof t === "number" ? new Date(t * 1000).toISOString() : ts;
}

/** 单份 equity 序列导出（单份视图与 V1 权益明细页签数据源——与副图
 *  同源同锚；bars=全区间合并序，函数内闭端过滤） */
export function perExecEquityPoints(
  exec: VrExecution,
  bars: FullRangeBar[],
): Array<{ time: string; value: number }> {
  return execAnchoredPoints(exec, bars);
}

/**
 * 全区间 equity 拼接（契约 C 主导出）：逐份锚序列拼接 + 段界 whitespace
 * 断点（段界重置不跨段连线——分段独立回测语义）。
 * 断点实现注记：whitespace 时间取下段首点 epoch−1s（严格落在两段之间）——
 * 库要求数据时间严格递增，与下段首值同时间戳的 whitespace 会构成重复
 * 时间项，故以「紧贴下段首 bar 之前」的一秒实现同一断线语义。
 */
export function buildEquitySeries(
  executions: VrExecution[],
  bars: FullRangeBar[],
): EquityPoint[] {
  return concatSegmentsWithBreaks(
    executions.map((exec) => execAnchoredPoints(exec, bars)),
    {
      firstTime: (seg) => seg[0].time,
      itemTime: (item) => item.time,
      makeBreak: (brk) => ({ time: new Date(brk * 1000).toISOString() }),
      toItems: (seg) => seg,
    },
  );
}

/** ── 契约 I：DNA/手动叠加指标拉取与装载 ── */

/** 主图指标线形制（与 KlineChart indicators 通道结构同形——id 同域去重） */
export interface IndicatorLine {
  id: string;
  type: string;
  color: string;
  data: Array<{ time: string; value: number }>;
}

export interface IndicatorLoadResult {
  state: "loading" | "ok" | "error";
  indicators: IndicatorLine[];
  /** 尾点校验不等（指标尾点 < K 线尾 bar——截断/缺数据，照画可用段） */
  tailLagged: boolean;
}

/** EMA 主图蓝系（CHART_VB.blue 同源）/BB 金系三带（middle 88 alpha 沿
 *  bollData 通道先例）/手动线 88 alpha 区分 DNA 线（契约 I/J） */
const EMA_DNA_COLOR = "#5B9DFF";
const EMA_MANUAL_COLOR = "#5B9DFF88";
const BB_UPPER_COLOR = "#FBBF24";
const BB_MIDDLE_COLOR = "#FBBF2488";
const BB_LOWER_COLOR = "#FBBF24";

import type { ChartIndicatorsResponse } from "@/types/api";

/**
 * 叠加指标拉取 hook（契约 I）：DNA 与手动叠加共用通道——输入 overlay
 * 列表（KlineReview 侧合并 DNA+手动并按 id 去重后传入），按当前视图
 * 周期与 K 线同区间拉取（start/end/limit 同锚）。请求聚合：全部 EMA
 * periods 一次请求（逗号串）、逐 BB 参数组各一次（n≤2 实际）。尾点
 * 校验：各线尾点 epoch >= K 线尾 bar epoch（klineLastEpoch），不等→
 * tailLagged=true（「指标数据不完整」提示+照画可用段）。error=指标源
 * 降级（K 线与 marker 不受阻——指标是叠加层非主数据）。
 */
export function useOverlayIndicators(
  overlays: Array<DnaOverlay & { manual?: boolean }>,
  symbol: string | null,
  timeframe: string | null,
  start: string | null,
  end: string | null,
  klineLastEpoch: number | null,
): IndicatorLoadResult {
  const emaPeriods = [...new Set(overlays.filter((o) => o.kind === "EMA").map((o) => o.period))];
  const bbGroups = [...new Map(
    overlays
      .filter((o): o is Extract<DnaOverlay, { kind: "BB" }> & { manual?: boolean } => o.kind === "BB")
      .map((o) => [`${o.period}:${o.std}`, o] as const),
  ).values()];
  const hasAny = emaPeriods.length > 0 || bbGroups.length > 0;

  const q = useQuery({
    queryKey: [
      "vr-overlay-indicators",
      symbol,
      timeframe,
      start,
      end,
      emaPeriods.join(","),
      bbGroups.map((b) => `${b.period}:${b.std}`).join(";"),
    ],
    queryFn: async () => {
      // 请求序携带组号：EMA 一发聚合+逐 BB 组各一发——响应显式对位（端点
      // boll 响应不回传参数，靠请求序映射）
      type Tagged = ({ kind: "ema" } | { kind: "bb"; group: number }) & ChartIndicatorsResponse;
      const reqs: Array<Promise<Tagged>> = [];
      if (emaPeriods.length > 0) {
        reqs.push(
          getChartIndicators(symbol!, timeframe!, {
            start: start ?? undefined,
            end: end ?? undefined,
            limit: FETCH_LIMIT,
            ema_periods: emaPeriods.join(","),
          }).then((r) => ({ kind: "ema" as const, ...r })),
        );
      }
      bbGroups.forEach((bb, group) => {
        reqs.push(
          getChartIndicators(symbol!, timeframe!, {
            start: start ?? undefined,
            end: end ?? undefined,
            limit: FETCH_LIMIT,
            boll_enabled: true,
            boll_period: bb.period,
            boll_std: bb.std,
          }).then((r) => ({ kind: "bb" as const, group, ...r })),
        );
      });
      return Promise.all(reqs);
    },
    enabled: !!symbol && !!timeframe && !!start && !!end && hasAny,
    retry: false,
  });

  if (!hasAny) return { state: "ok", indicators: [], tailLagged: false };
  if (q.isError) return { state: "error", indicators: [], tailLagged: false };
  if (q.isLoading || !q.data) return { state: "loading", indicators: [], tailLagged: false };

  const manualEma = new Set(
    overlays
      .filter((o): o is Extract<DnaOverlay, { kind: "EMA" }> & { manual?: boolean } => o.kind === "EMA" && !!o.manual)
      .map((o) => o.period),
  );
  const manualBb = new Set(
    overlays
      .filter((o): o is Extract<DnaOverlay, { kind: "BB" }> & { manual?: boolean } => o.kind === "BB" && !!o.manual)
      .map((o) => `${o.period}:${o.std}`),
  );
  const indicators: IndicatorLine[] = [];
  let tailLagged = false;
  const checkTail = (line: Array<{ time: string; value: number }>) => {
    if (line.length === 0) return;
    const t = toTime(line[line.length - 1].time);
    if (klineLastEpoch != null && typeof t === "number" && t < klineLastEpoch) tailLagged = true;
  };
  for (const resp of q.data) {
    if (resp.kind === "ema") {
      for (const [period, pts] of Object.entries(resp.ema ?? {})) {
        if (!pts) continue;
        checkTail(pts);
        indicators.push({
          id: `dna_ema_${period}`,
          type: "ema",
          color: manualEma.has(Number(period)) ? EMA_MANUAL_COLOR : EMA_DNA_COLOR,
          data: pts,
        });
      }
    } else {
      const group = bbGroups[resp.group];
      if (!group || resp.boll == null) continue;
      const manual = manualBb.has(`${group.period}:${group.std}`);
      const prefix = `dna_bb_${group.period}_${group.std}`;
      for (const [band, color] of [
        ["upper", BB_UPPER_COLOR],
        ["middle", BB_MIDDLE_COLOR],
        ["lower", BB_LOWER_COLOR],
      ] as const) {
        const pts = resp.boll[band] ?? [];
        checkTail(pts);
        indicators.push({
          id: `${prefix}_${band}`,
          type: "line",
          color: manual ? EMA_MANUAL_COLOR : color,
          data: pts,
        });
      }
    }
  }
  return { state: "ok", indicators, tailLagged };
}
