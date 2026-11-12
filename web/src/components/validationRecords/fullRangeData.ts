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
 */
import { useQuery } from "@tanstack/react-query";
import { getOhlcvBySymbol } from "@/services/datasets";
import { toTime } from "@/components/charts/core/time";
import type { VrExecution } from "@/services/validationRecords";

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
function epochOf(ts: string): number | null {
  const t = toTime(ts);
  return typeof t === "number" ? t : null;
}

/** ── 契约 A：四态判别联合 ── */

export interface MismatchedExec {
  exec_ref: string;
  bars: number;
  points: number;
}

export type FullRangeResult =
  | { state: "loading" }
  | { state: "error" }
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
): FullRangeResult {
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
    queryKey: ["vr-kline-full", symbol, timeframe, bounds.minStart, bounds.maxEnd],
    queryFn: () =>
      getOhlcvBySymbol(symbol!, timeframe!, {
        start: bounds.minStart ?? undefined,
        end: bounds.maxEnd ?? undefined,
        limit: FETCH_LIMIT,
      }),
    enabled: !!symbol && !!timeframe && !!bounds.minStart && !!bounds.maxEnd,
  });

  if (q.isError) return { state: "error" };
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
  if (endEpoch != null && (lastEpoch == null || lastEpoch < endEpoch)) {
    const tfSec = timeframe ? timeframeSeconds(timeframe) : 0;
    const missing = lastEpoch != null && tfSec > 0
      ? Math.max(1, Math.round((endEpoch - lastEpoch) / tfSec))
      : null;
    return { state: "truncated", candles, truncatedMissing: missing, mismatchedExecs: [], execAligned: {} };
  }

  // 按份对账（三型统一：每执行份闭端 bar 数==该份 equity.values.length）
  const mismatchedExecs: MismatchedExec[] = [];
  const execAligned: Record<string, boolean> = {};
  for (const e of executions) {
    const s = epochOf(e.range_start);
    const t = epochOf(e.range_end);
    const bars = candles.filter(
      (c) => {
        const ce = epochOf(c.timestamp);
        return ce != null && s != null && t != null && ce >= s && ce <= t;
      },
    ).length;
    const aligned = bars === e.equity.values.length;
    execAligned[e.exec_ref] = aligned;
    if (!aligned) mismatchedExecs.push({ exec_ref: e.exec_ref, bars, points: e.equity.values.length });
  }
  if (mismatchedExecs.length > 0) {
    return { state: "mismatch", candles, truncatedMissing: null, mismatchedExecs, execAligned };
  }
  return { state: "ok", candles, truncatedMissing: null, mismatchedExecs: [], execAligned };
}

/** ── 契约 C：equity 序列构建 ── */

/** whitespace 断点项（无 value——lightweight-charts 官方断线机制） */
export type EquityPoint = { time: string; value: number } | { time: string };

/** 单份锚序列：zip 主路径（对账通过）或留痕自锚降级（timestamps 直用/
 *  compact 等距重建——原 equityPoints 逻辑内化，时间产出统一 ISO 带 Z） */
function execAnchoredPoints(exec: VrExecution, bars: FullRangeBar[]): Array<{ time: string; value: number }> {
  const values = exec.equity.values;
  if (values.length === 0) return [];
  const s = epochOf(exec.range_start);
  const t = epochOf(exec.range_end);
  const inRange = (s != null && t != null)
    ? bars.filter((b) => {
        const be = epochOf(b.timestamp);
        return be != null && be >= s && be <= t;
      }).map((b) => b.timestamp)
    : [];
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
  const out: EquityPoint[] = [];
  let lastEpoch: number | null = null;
  for (const exec of executions) {
    const pts = execAnchoredPoints(exec, bars);
    if (pts.length === 0) continue;
    const firstEpoch = epochOf(pts[0].time);
    if (out.length > 0 && firstEpoch != null) {
      const brk = firstEpoch - 1;
      if (lastEpoch != null && brk > lastEpoch) out.push({ time: new Date(brk * 1000).toISOString() });
    }
    for (const p of pts) {
      out.push(p);
      const pe = epochOf(p.time);
      if (pe != null) lastEpoch = pe;
    }
  }
  return out;
}
