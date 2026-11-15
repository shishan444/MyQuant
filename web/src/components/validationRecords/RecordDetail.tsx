/**
 * 验证记录明细复核四形态（upgrade-validation-records-list 5.2 +
 * upgrade-vr-fullrange-chart 全区间重构）。D1 参数回显两行（statement
 * 全文逐字+union-balance 余量——各自数据源不可达各自降级）；D2 K 线
 * 复核=全区间单张连续图（契约 A 四态装载/B 段界竖带/C equity 副图同轴/
 * D marker 单源带价+crosshair 读数/E 单份筛选前端过滤/F trail 拼接）；
 * D3 资金曲线+一致性核对行（isolated 恒等断言、random/staged worst
 * 窗口/最弱段统计身份标签——数据源与副图同锚）；V1 三页签结构驱动恒三
 * （权益明细页签与 K 线同锚）；V2 逐笔表（开平双价/未平仓如实/色点单源
 * 三视图同色）；V3 信号回溯（四元判读+reason 五值两族+capped 披露+MTF
 * 门控提示行）；V4 三布尔独立降级（null=代际陈述中性小标、缺陷态异常
 * 带 record_id）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useStrategy } from "@/hooks/useStrategies";
import { InputNumber, Select, Switch, Tabs } from "antd";
import { FileSearch } from "lucide-react";
import { KlineChart, type KlineChartHandle } from "@/components/charts/KlineChart";
import { attachSegmentBand, toSegmentBands } from "@/components/charts/primitives/segmentBand";
import { formatPercent, formatTimestamp } from "@/lib/utils";
import { ApiError } from "@/services/api";
import { CHART_VC } from "@/theme/tokens";
import {
  barsInExecRange,
  buildEquitySeries,
  concatSegmentsWithBreaks,
  epochOf,
  perExecEquityPoints,
  timeframeSeconds,
  useFullRangeKline,
  useOverlayIndicators,
  type EquityPoint,
  type FullRangeBar,
  type FullRangeResult,
} from "./fullRangeData";
import {
  getUnionBalance,
  getValidationRecordDetail,
  type ValidationDetailResponse,
  type ValidationRecordRow,
  type VrExecution,
  type VrTraceRow,
} from "@/services/validationRecords";
import {
  LAYER_NOTE,
  OUTCOME_LABELS,
  OVERLAY_MTF_TEXT,
  OVERLAY_SUBCHART_TEXT,
  OVERLAY_UNSUPPORTED_TEXT,
  REASON_LABELS,
  TRACE_FAMILY_COLOR,
  dnaIndicators,
  eventSemantics,
  legacySide,
  markerSide,
  recordSymbolTimeframe,
  resolveDirectionLookup,
  snapToBars,
  traceMarkers,
  type DnaIndicatorExtract,
  type TraceMarkerDraft,
} from "./traceSemantics";

const VERDICT_COLOR: Record<string, string> = {
  pass: "text-profit", fail: "text-loss", inconclusive: "text-attention-gold",
};

/** 视图状态：全区间（缺省主视图）| 指定执行份（单份筛选前端过滤） */
type ViewSel = "all" | number;

/** epoch 秒 → ISO 带 Z 串 */
const isoOf = (sec: number) => new Date(sec * 1000).toISOString();

/** 视图周期八档常量（契约 K——A1 全集，数据目录实有档） */
const VIEW_TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "3d"] as const;

/** BB field 徽标中文（契约 L——如「BB(20,2)·中轨」） */
const BB_FIELD_LABEL: Record<string, string> = {
  upper: "上轨", middle: "中轨", lower: "下轨",
};

/** 值点分段吸附（契约 K）：逐段吸附到目标 bar（同 bar 保尾值）+段间断点
 *  重算（epoch−1 守卫）——段界重置语义不变（吸附只改时间锚） */
function snapSeriesParts(
  parts: Array<Array<{ time: string; value: number }>>,
  bars: FullRangeBar[],
  tfSec: number,
): EquityPoint[] {
  const segs = parts.map((part) => {
    if (part.length === 0) return [];
    const r = snapToBars(part.map((p) => p.time), bars, tfSec);
    const toOf = new Map(r.snapped.map((p) => [p.from, p.to]));
    const seg: Array<{ time: string; value: number }> = [];
    for (const p of part) {
      const to = toOf.get(p.time);
      if (to === undefined) continue;
      const last = seg[seg.length - 1];
      if (last && last.time === to) last.value = p.value;
      else seg.push({ time: to, value: p.value });
    }
    return seg;
  });
  return concatSegmentsWithBreaks(segs, {
    firstTime: (seg) => seg[0].time,
    itemTime: (item) => item.time,
    makeBreak: (brk) => ({ time: isoOf(brk) }),
    toItems: (seg) => seg,
  });
}

/** trail 值点分段吸附（契约 K）——trail 通道断点为 null 值点（与 equity
 *  whitespace 同语义），段内未定义期在粗粒度下并段（保尾值） */
function snapTrailParts(
  parts: Array<Array<{ time: string; value: number }>>,
  bars: FullRangeBar[],
  tfSec: number,
): Array<{ time: string; value: number | null }> {
  const segs = parts.map((part) => {
    if (part.length === 0) return [];
    const r = snapToBars(part.map((p) => p.time), bars, tfSec);
    const toOf = new Map(r.snapped.map((p) => [p.from, p.to]));
    const seg: Array<{ time: string; value: number }> = [];
    for (const p of part) {
      const to = toOf.get(p.time);
      if (to === undefined) continue;
      const last = seg[seg.length - 1];
      if (last && last.time === to) last.value = p.value;
      else seg.push({ time: to, value: p.value });
    }
    return seg;
  });
  return concatSegmentsWithBreaks(segs, {
    firstTime: (seg) => seg[0].time,
    itemTime: (item) => item.time,
    makeBreak: (brk) => ({ time: isoOf(brk), value: null }),
    toItems: (seg) => seg,
  });
}

/** trail 全区间拼接（契约 F）：各份 trail_series concat+段界 null 断点
 *  （原语层产 whitespace 真断线——跨段连线误导）；单份原样返回 */
function concatTrail(
  executions: VrExecution[],
): Array<{ time: string; value: number | null }> | null {
  const parts = executions
    .map((e) => e.trail_series ?? [])
    .filter((p) => p.length > 0);
  if (parts.length === 0) return null;
  return concatSegmentsWithBreaks(parts, {
    firstTime: (seg) => seg[0].time,
    itemTime: (item) => item.time,
    makeBreak: (brk) => ({ time: isoOf(brk), value: null }),
    toItems: (seg) => seg.map((p) => ({ time: p.time, value: p.value ?? null })),
  });
}

const fmtTime = (t?: string | null) => formatTimestamp(t, "minute");
const unk = (v: number | null) => (v == null ? "未知" : String(v));

/* ── V4 三布尔能力徽标（独立降级——true 正常/false 缺陷带 record_id/
    null 代际中性小标；呈现于详情头部四视图共用） ── */
function CapabilityBadges({ d }: { d: ValidationDetailResponse }) {
  const items: Array<{ label: string; v: boolean | null }> = [
    { label: "信号留痕", v: d.signal_trace },
    { label: "交易语义", v: d.trade_semantics },
    { label: "止损价", v: d.stop_price },
  ];
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2" data-testid="vr-capability-badges">
      {items.map((it) => (
        <span
          key={it.label}
          data-testid={`vr-cap-${it.label}`}
          className={`rounded border px-1.5 py-0.5 text-[10px] ${
            it.v === true
              ? "border-profit/30 text-profit"
              : it.v === false
                ? "border-loss/40 text-loss"
                : "border-border-faint text-text-muted"
          }`}
          title={
            it.v === true
              ? "能力已执行"
              : it.v === false
                ? `管线执行失败（缺陷态 W0）· ${d.record_id}`
                : "产生于信号留痕能力上线前"
          }
        >
          {it.v === true ? "✓" : it.v === false ? "✗" : "◐"} {it.label}
          {it.v === null ? "·上线前" : ""}
        </span>
      ))}
    </div>
  );
}

/* ── D1 参数回显（两行：statement 全文逐字+union-balance 余量行——
    各自数据源不可达各自降级） ── */
function ParamsEcho({
  record,
  detail,
}: {
  record: ValidationRecordRow;
  detail: ValidationDetailResponse;
}) {
  const { symbol, timeframe } = recordSymbolTimeframe(record);
  // adhoc 记录（hypothesis_id="adhoc"）无假设账本——不发起查询直接降级
  const eligible =
    !!record.hypothesis_id &&
    record.hypothesis_id !== "adhoc" &&
    !!symbol &&
    !!timeframe;
  const q = useQuery({
    queryKey: ["vr-union-balance", record.hypothesis_id, symbol, timeframe],
    queryFn: () =>
      getUnionBalance({
        hypothesis_id: record.hypothesis_id,
        symbol: symbol!,
        timeframe: timeframe!,
      }),
    enabled: eligible,
    retry: false,
  });
  return (
    <div className="mb-2 space-y-1 text-xs" data-testid="vr-params-echo">
      {/* 行 1：statement 全文逐字（无假设关联→「假设不可达」降级） */}
      <p className="leading-relaxed text-text-secondary" data-testid="vr-statement-full">
        {record.statement ? `「${record.statement}」` : "假设不可达（无关联假设原话）"}
      </p>
      {/* 行 2：union-balance 余量（不可达→「余量不可达」降级） */}
      <p className="flex flex-wrap items-baseline gap-x-3 font-num text-[10px] text-text-muted">
        <span data-testid="vr-union-balance">
          {eligible
            ? q.isError || q.isLoading
              ? q.isError
                ? "并集余量不可达"
                : "并集余量查询中…"
              : `并集余量 ${q.data!.remaining_bars} bar · 剩余 ${q.data!.remaining.length} 段`
            : "并集余量不可达"}
        </span>
        <span>
          {symbol ?? "—"} · {timeframe ?? "—"} · 资金 {unk(detail.init_cash)} · 费率{" "}
          {unk(detail.fee)} · 滑点 {unk(detail.slippage)}
        </span>
      </p>
    </div>
  );
}

/* ── D2 K 线复核（全区间单图：契约 A 四态装载/B 段界竖带/C equity 副图/
    D marker 单源带价+crosshair/E 单份筛选/F trail 拼接） ── */
function KlineReview({
  executions,
  fr,
  view,
  symbol,
  timeframe,
  stopPrice,
  chartRef,
  extract,
  hasStrategy,
  viewTf,
  deadTfs,
  onViewTf,
  recordDirection,
}: {
  executions: VrExecution[];
  fr: FullRangeResult;
  view: ViewSel;
  symbol: string | null;
  timeframe: string | null;
  stopPrice: boolean | null;
  chartRef: React.RefObject<KlineChartHandle | null>;
  /** DNA 指标三分判别（契约 H——strategy_id null 时三分全空） */
  extract: DnaIndicatorExtract;
  hasStrategy: boolean;
  /** 视图周期（契约 K：null=基准；切换仅查看粒度，执行语义锚恒基准） */
  viewTf: string | null;
  deadTfs: ReadonlySet<string>;
  onViewTf: (tf: string | null) => void;
  /** 记录级方向原样透传（fe-vr-marker-fidelity 契约 5：四态
   *  undefined/"long"/"short"/"mixed"——值域判定在解析器内单点） */
  recordDirection: string | undefined;
}) {
  const execIdx = view === "all" ? -1 : view;
  const exec = execIdx >= 0 ? executions[execIdx] : null;
  // P-2 图高视口化：55% 视口高、420px 下限（矮窗口防不可用）——
  // 局部 resize 响应，不引全局依赖
  const [chartHeight, setChartHeight] = useState(() =>
    Math.max(Math.round(window.innerHeight * 0.55), 420),
  );
  useEffect(() => {
    const onResize = () =>
      setChartHeight(Math.max(Math.round(window.innerHeight * 0.55), 420));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const bars: FullRangeBar[] = useMemo(
    () => (fr.state === "loading" || fr.state === "error" ? [] : fr.candles),
    [fr],
  );
  const chartReady = fr.state !== "loading" && fr.state !== "error";

  // ── 契约 J：手动叠加开关（页面局部 state——不入 chart-settings 全局 store） ──
  const [emaOn, setEmaOn] = useState(false);
  const [emaPeriod, setEmaPeriod] = useState<number>(50);
  const [bbOn, setBbOn] = useState(false);
  const [bbPeriod, setBbPeriod] = useState<number>(20);
  const [bbStd, setBbStd] = useState<number>(2.0);

  // ── 契约 I：叠加指标拉取（DNA+手动合并，id 同域去重——同参数手动线并入 DNA 线） ──
  const minStart = executions.length
    ? executions.reduce((acc, e) => (acc == null || e.range_start < acc ? e.range_start : acc), null as string | null)
    : null;
  const maxEnd = executions.length
    ? executions.reduce((acc, e) => (acc == null || e.range_end > acc ? e.range_end : acc), null as string | null)
    : null;
  const overlays = useMemo(() => {
    const list: Array<import("./traceSemantics").DnaOverlay & { manual?: boolean }> = [...extract.overlayable];
    if (bbOn && !list.some((o) => o.kind === "BB" && o.period === bbPeriod && o.std === bbStd)) {
      list.push({ kind: "BB", period: bbPeriod, std: bbStd, manual: true });
    }
    if (emaOn && !list.some((o) => o.kind === "EMA" && o.period === emaPeriod)) {
      list.push({ kind: "EMA", period: emaPeriod, manual: true });
    }
    return list;
  }, [extract.overlayable, emaOn, emaPeriod, bbOn, bbPeriod, bbStd]);
  const klineLastEpoch = bars.length ? epochOf(bars[bars.length - 1].timestamp) : null;
  const ind = useOverlayIndicators(
    overlays,
    symbol,
    viewTf ?? timeframe,
    minStart,
    maxEnd,
    klineLastEpoch,
  );

  // ── 契约 K：周期切换吸附（基准周期视图短路直挂——零吸附开销） ──
  const snapActive = viewTf != null && timeframe != null && viewTf !== timeframe && bars.length > 0;
  const snapTfSec = timeframeSeconds(viewTf ?? timeframe ?? "");
  // 契约 E：单份视图主图只画该份闭端区间（渲染层过滤——queryKey 与拉取
  // 仍全区间；与对账/equity 锚序列共用 barsInExecRange 单源）
  const viewCandles = exec ? barsInExecRange(bars, exec) : bars;

  // 契约 C：equity 副图数据（全份拼接带段界断点/单份序列无断点——同源同锚；
  // 高周期视图=留痕自锚独立重建+吸附，值与段界重置语义不变）
  const equityData = useMemo(() => {
    if (snapActive) {
      const parts = (exec ? [exec] : executions).map((e) => perExecEquityPoints(e, []));
      return snapSeriesParts(parts, bars, snapTfSec);
    }
    return exec ? perExecEquityPoints(exec, bars) : buildEquitySeries(executions, bars);
  }, [exec, executions, bars, snapActive, snapTfSec]);
  // 契约 D：marker 单源合并（traceMarkers 带价方向代入；全份 flatMap 按
  // time 升序；每份独立查表闭包——跨份共享边界 bar 的 marker 各自配对
  // 本份桶；高周期视图吸附到所在 bar——左闭右开，超域丢弃计数披露）
  const markersRaw = useMemo(() => {
    const src = exec ? [exec] : executions;
    return src
      .flatMap((e) => traceMarkers(e.trace_rows, resolveDirectionLookup(recordDirection, e)))
      .sort((a, b) => (epochOf(a.time) ?? 0) - (epochOf(b.time) ?? 0));
  }, [exec, executions, recordDirection]);
  // 侧位判定（fe-vr-marker-fidelity 契约 4/5，吸附之后——R5）：barMap
  // 建自 bars 数组（与 snapToBars 同一数组：基准视图=基准 bar、吸附
  // 视图=高周期 bar）；有价且 bar 命中 → markerSide 就近侧位（≥中点
  // 标上），无价/bar 缺失 → legacySide 旧惯例（入场族下方/出场族上方）
  const markers = useMemo(() => {
    const barMap = new Map(bars.map((b) => [epochOf(b.timestamp), b]));
    const locate = (m: TraceMarkerDraft): TraceMarkerDraft & { position: "aboveBar" | "belowBar" } => {
      const bar = barMap.get(epochOf(m.time) ?? NaN);
      const position = m.price != null && bar ? markerSide(m.price, bar) : legacySide(m.action);
      return { ...m, position };
    };
    if (!snapActive) return { list: markersRaw.map(locate), dropped: 0 };
    const r = snapToBars(markersRaw.map((m) => m.time), bars, snapTfSec);
    const toOf = new Map(r.snapped.map((p) => [p.from, p.to]));
    return {
      list: markersRaw
        .filter((m) => toOf.has(m.time))
        .map((m) => locate({ ...m, time: toOf.get(m.time)! })),
      dropped: r.dropped,
    };
  }, [markersRaw, snapActive, snapTfSec, bars]);
  // 契约 F：trail 挂线（全份拼接带段界断点/单份现状行为；高周期视图
  // 值点分段吸附+段界断点重算）
  const trail = useMemo(() => {
    const parts = (exec ? [exec] : executions).map((e) =>
      (e.trail_series ?? []).filter(
        (p) => p.value != null && Number.isFinite(p.value as number),
      ) as Array<{ time: string; value: number }>,
    );
    if (snapActive) {
      const snapped = snapTrailParts(parts, bars, snapTfSec);
      return snapped.length > 0 ? snapped : null;
    }
    return exec ? exec.trail_series : concatTrail(executions);
  }, [exec, executions, snapActive, snapTfSec, bars]);
  useEffect(() => {
    const chart = chartRef.current;
    chart?.setObservationSeries(
      trail ? trail.map((p) => ({ time: p.time, value: p.value ?? null })) : null,
    );
    return () => chart?.setObservationSeries(null);
  }, [trail, chartRef, chartReady]);

  // 契约 B：段界竖带（多带单控制器；单份视图只画选中段界）
  const bandCtlRef = useRef<{ setData: (b: ReturnType<typeof toSegmentBands> | null) => void } | null>(null);
  const bands = useMemo(
    () => toSegmentBands(exec ? [exec] : executions),
    [exec, executions],
  );
  useEffect(() => {
    bandCtlRef.current?.setData(bands);
  }, [bands]);
  // 契约 D：crosshair 读数（无悬停显空；OHLC 由 viewCandles 按 param.time
  // 查行——与主图同源，单份视图只在闭端区间内可查）
  const [hover, setHover] = useState<{ time: string; o: number; h: number; l: number; c: number } | null>(null);
  const onCrosshairMove = useCallback((param: { time?: unknown }) => {
    if (param.time == null || typeof param.time !== "number") {
      setHover(null);
      return;
    }
    const bar = viewCandles.find((b) => epochOf(b.timestamp) === param.time);
    setHover(bar ? { time: bar.timestamp, o: bar.open, h: bar.high, l: bar.low, c: bar.close } : null);
  }, [viewCandles]);

  if (!symbol || !timeframe) {
    return (
      <p className="text-xs text-text-muted">
        行 params 无 symbol/timeframe——K 线区间不可定
      </p>
    );
  }
  if (fr.state === "error") {
    return <p className="text-xs text-loss">K 线源暂不可达（稍后自动重试）</p>;
  }
  if (fr.state === "loading") {
    return <p className="text-xs text-text-muted">K 线装载中…</p>;
  }

  // 契约 A 四态提示（ok 态无提示——截断与对账不等各自如实提示不静默）
  const stateMsg =
    fr.state === "truncated"
      ? `K 线数据被截断（limit 50000）：区间尾部 ${fr.truncatedMissing ?? "?"} 根缺失`
      : fr.state === "mismatch"
        ? fr.mismatchedExecs
            .map((m) => `份 ${m.exec_ref}：K 线 ${m.bars} 根 vs 权益 ${m.points} 点`)
            .join("；")
        : null;

  const rangeLabel = exec
    ? `${exec.range_start || "?"} → ${exec.range_end || "?"}`
    : `${executions[0]?.range_start || "?"} → ${executions[executions.length - 1]?.range_end || "?"}（全区间 ${executions.length} 份）`;
  const filledCount = (exec ? [exec] : executions).reduce(
    (n, e) => n + e.trace_rows.filter((r) => r.outcome === "filled").length,
    0,
  );
  const tradesCount = (exec ? [exec] : executions).reduce((n, e) => n + e.trades.length, 0);

  // 契约 L：DNA 徽标组（overlayable 实参+subChartOnly/unsupported 降级 tooltip）
  const dnaBadges = (
    <span className="flex flex-wrap items-center gap-1" data-testid="vr-dna-badges">
      {!hasStrategy && (
        <span className="rounded border border-border-faint px-1 text-[10px] text-text-muted" title="无策略关联">
          无策略关联——DNA 指标叠加不可用
        </span>
      )}
      {hasStrategy && extract.overlayable.length === 0 && extract.subChartOnly.length === 0 && extract.unsupported.length === 0 && (
        <span className="rounded border border-border-faint px-1 text-[10px] text-text-muted">
          DNA 无引用指标
        </span>
      )}
      {extract.overlayable.map((o) => (
        <span
          key={o.kind === "EMA" ? `ema${o.period}` : `bb${o.period}_${o.std}`}
          className="rounded border px-1 text-[10px]"
          style={{ color: CHART_VC.teal, borderColor: `${CHART_VC.teal}44` }}
          title="DNA 引用指标（参数与策略一致）"
        >
          {o.kind === "EMA" ? `EMA(${o.period})` : `BB(${o.period},${o.std})${o.field ? `·${BB_FIELD_LABEL[o.field] ?? o.field}` : ""}`}
        </span>
      ))}
      {extract.subChartOnly.map((o) => (
        <span
          key={o.kind}
          className="rounded border border-attention-gold/40 px-1 text-[10px] text-attention-gold"
          title={OVERLAY_SUBCHART_TEXT}
        >
          {o.kind}
        </span>
      ))}
      {extract.unsupported.map((o, i) => (
        <span
          key={`${o.indicator}-${i}`}
          className="rounded border border-border-faint px-1 text-[10px] text-text-muted"
          title={o.note ?? OVERLAY_UNSUPPORTED_TEXT}
        >
          {o.note === OVERLAY_MTF_TEXT ? `${LAYER_NOTE}·${o.indicator}` : o.indicator}
        </span>
      ))}
      {ind.state === "loading" && (
        <span className="text-[10px] text-text-muted">指标装载中…</span>
      )}
      {ind.state === "error" && (
        <span className="rounded border border-attention-gold/40 px-1 text-[10px] text-attention-gold">
          指标源暂不可达（叠加降级）
        </span>
      )}
      {ind.state === "ok" && ind.tailLagged && (
        <span className="rounded border border-attention-gold/40 px-1 text-[10px] text-attention-gold" title="指标尾点早于 K 线尾 bar——截断/缺数据">
          指标数据不完整
        </span>
      )}
    </span>
  );

  return (
    <div
      className="rounded p-2"
      style={{ backgroundColor: CHART_VC.bg, border: `1px solid ${CHART_VC.teal}22` }}
      data-testid="vr-kline-review"
      data-record-direction={recordDirection ?? ""}
    >
      <p className="mb-1 text-[10px]" style={{ color: CHART_VC.dim }}>
        {symbol} · {timeframe} · {rangeLabel} ·
        标记 {markers.list.length}（filled 留痕 {filledCount} 行 · trades {tradesCount} 笔）
        {snapActive && markers.dropped > 0 && `（${markers.dropped} 个标记超出区间）`}
        <span className="ml-2" data-testid="vr-view-mode">
          {exec ? `单份 ${exec.exec_ref}` : "全区间"}
        </span>
      </p>
      {/* 契约 L 工具区：crosshair 读数（左）｜周期 Select+基准徽标+手动开关（右） */}
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px]" style={{ color: CHART_VC.dim }} data-testid="vr-crosshair-info">
          {hover
            ? `${hover.time}  O ${hover.o} H ${hover.h} L ${hover.l} C ${hover.c}`
            : "—"}
        </p>
        <span className="flex flex-wrap items-center gap-1.5 text-[10px]" data-testid="vr-toolbar">
          <span className="rounded border border-border-faint px-1 text-text-muted" data-testid="vr-base-tf">
            基准 {timeframe ?? "—"}
          </span>
          <Select
            size="small"
            className="!w-20"
            value={viewTf ?? timeframe ?? undefined}
            onChange={(v) => onViewTf(v === timeframe ? null : v)}
            options={VIEW_TFS.map((tf) => ({
              value: tf,
              label: tf,
              disabled: tf !== timeframe && deadTfs.has(tf),
            }))}
            data-testid="vr-tf-select"
          />
          <Switch size="small" checked={emaOn} onChange={setEmaOn} data-testid="vr-manual-ema" />
          <span className="text-text-muted">EMA</span>
          <InputNumber
            size="small"
            className="!w-14"
            min={1}
            max={500}
            value={emaPeriod}
            onChange={(v) => setEmaPeriod(Number(v) || 50)}
            disabled={!emaOn}
          />
          <Switch size="small" checked={bbOn} onChange={setBbOn} data-testid="vr-manual-bb" />
          <span className="text-text-muted">BB</span>
          <InputNumber
            size="small"
            className="!w-12"
            min={1}
            max={500}
            value={bbPeriod}
            onChange={(v) => setBbPeriod(Number(v) || 20)}
            disabled={!bbOn}
          />
          <InputNumber
            size="small"
            className="!w-12"
            min={0.1}
            max={5}
            step={0.5}
            value={bbStd}
            onChange={(v) => setBbStd(Number(v) || 2.0)}
            disabled={!bbOn}
          />
        </span>
      </div>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        {dnaBadges}
      </div>
      <p className="mb-1 flex flex-wrap gap-x-3 text-[10px]" style={{ color: CHART_VC.dim }}>
        <span style={{ color: TRACE_FAMILY_COLOR.strategy }}>标记=按方向生成的字义+成交价（蓝=策略动作：开多/开空/加仓/减仓/平多/平空，</span>
        <span style={{ color: TRACE_FAMILY_COLOR.risk }}>金=风控出场：平多/平空）</span>
        <span>止损线{trail ? "（trail 真值）" : stopPrice === null ? "（产生于能力上线前）" : "（非 trail 档无序列）"}</span>
      </p>
      {stateMsg && (
        <p className="mb-1 rounded border border-attention-gold/40 px-2 py-0.5 text-[10px] text-attention-gold" data-testid="vr-kline-state">
          {stateMsg}
        </p>
      )}
      {viewCandles.length === 0 ? (
        <p className="py-6 text-center text-xs" style={{ color: CHART_VC.dim }}>
          该区间无 K 线数据
        </p>
      ) : (
        <KlineChart
          data={viewCandles}
          signalMarkers={markers.list}
          height={chartHeight}
          ref={chartRef}
          subChartType="equity"
          equityData={equityData}
          indicators={ind.state === "ok" ? ind.indicators : undefined}
          hideLegend
          onChartReady={(_, series) => {
            bandCtlRef.current = attachSegmentBand(series);
            bandCtlRef.current.setData(bands);
          }}
          onCrosshairMove={onCrosshairMove}
        />
      )}
    </div>
  );
}

/* ── D3 资金曲线+一致性核对行（数据源与副图同锚——perExecEquityPoints） ── */
function EquityReview({
  exec,
  bars,
  record,
}: {
  exec: VrExecution;
  bars: FullRangeBar[];
  record: ValidationRecordRow;
}) {
  const pts = perExecEquityPoints(exec, bars);
  const eq = exec.equity;
  if (pts.length === 0) {
    return (
      <p className="text-xs text-text-muted" data-testid="vr-equity">
        该执行份无 equity 留痕（空曲线——零 bar 区间如实呈现）
      </p>
    );
  }
  const compact = eq?.grid_aligned === true;
  const first = pts[0].value;
  const last = pts[pts.length - 1].value;
  const delta = ((last - first) / first) * 100;

  // 一致性核对：isolated 恒等断言（engine 口径 total_return=末值/首值-1
  // 重算对照）；random/staged 统计身份标签（当前份是否 worst 份）
  let consistency: { text: string; ok: boolean | null } | null = null;
  const result = (record.result ?? {}) as Record<string, unknown>;
  if (record.level === "isolated_backtest") {
    const claimed = (result.metrics as { total_return?: number } | undefined)
      ?.total_return;
    if (typeof claimed === "number") {
      const ok = Math.abs(delta / 100 - claimed) < 1e-6;
      consistency = { text: `与声称收益一致性核对：终值 ${formatPercent(claimed)}`, ok };
    }
  } else if (record.level === "random_window") {
    const worst = (result.summary as
      | { worst_window?: { start?: string } }
      | undefined)?.worst_window;
    // worst_window.start 带 T 时间戳、range_start 日期串——天粒度对齐
    const isWorst = worst?.start?.slice(0, 10) === exec.range_start.slice(0, 10);
    consistency = {
      text: `窗口 ${exec.exec_ref}${isWorst ? "（最差窗口）" : ""}`,
      ok: null,
    };
  } else if (record.level === "staged_validation") {
    const worst = (result.summary as
      | { worst_segment?: { label?: string } }
      | undefined)?.worst_segment;
    consistency = {
      text: `段 ${exec.exec_ref}${worst?.label === exec.exec_ref ? "（最弱段）" : ""}`,
      ok: null,
    };
  }
  return (
    <div className="flex flex-col gap-1.5 text-xs" data-testid="vr-equity">
      <p className="text-[10px] text-text-muted">
        {compact
          ? `compact 形制（start=${eq.start} + ${eq.length}×${eq.timeframe} 时间轴重建）`
          : "全时间戳形制（timestamps 直用）"}
        · {pts.length} 点 · {exec.trades.length} 笔
      </p>
      <div className="flex flex-wrap items-baseline gap-4">
        <span className="font-num text-text-secondary">首 {first.toFixed(1)}</span>
        <span className="font-num text-text-secondary">末 {last.toFixed(1)}</span>
        <span className={`font-num font-medium ${delta >= 0 ? "text-profit" : "text-loss"}`}>
          {delta >= 0 ? "+" : ""}{delta.toFixed(2)}%
        </span>
        {consistency && (
          <span
            className={`text-[10px] ${consistency.ok === true ? "text-profit" : consistency.ok === false ? "text-loss" : "text-text-muted"}`}
            data-testid="vr-equity-consistency"
          >
            {consistency.text}
            {consistency.ok === true ? " ✓" : consistency.ok === false ? " ✗ 不一致" : ""}
          </span>
        )}
      </div>
      {/* 逐点序列抽样呈现（前 12 点+末点——完整序列在权益明细页签） */}
      <div className="grid grid-cols-3 gap-x-3 gap-y-0.5 font-num text-[10px] text-text-muted">
        {pts.slice(0, 12).map((p, i) => (
          <span key={`${p.time}-${i}`} className="truncate" title={p.time}>
            {p.value.toFixed(1)}
          </span>
        ))}
        {pts.length > 12 && (
          <span className="text-text-muted">… 共 {pts.length} 点（末 {last.toFixed(1)}）</span>
        )}
      </div>
    </div>
  );
}

/* ── V2 成交记录逐笔表（开平双价/未平仓如实/色点单源；点行定位） ── */
function TradeTable({
  exec,
  tradeSemantics,
  onLocate,
}: {
  exec: VrExecution;
  tradeSemantics: boolean | null;
  onLocate: (time: string) => void;
}) {
  // 出场锚 → trace 对齐（同 bar 出场保留标注：exit 类事件码全集收集）
  const exitAnchors = useMemo(() => {
    const map = new Map<string, VrTraceRow[]>();
    for (const r of exec.trace_rows) {
      const sem = eventSemantics(r.event_code);
      if (sem?.action === "exit") {
        const key = r.bar_time.slice(0, 16);
        const list = map.get(key);
        if (list) list.push(r);
        else map.set(key, [r]);
      }
    }
    return map;
  }, [exec.trace_rows]);

  return (
    <div className="text-xs" data-testid="vr-trades-table">
      <p className="mb-1 text-[10px] text-text-muted">
        共 {exec.trades.length} 笔（滚动）· 点行定位图中蜡烛
        {tradeSemantics === null && " · 交易语义产生于能力上线前（动作标注按成交对齐推导）"}
        {tradeSemantics === false && " · 交易语义管线执行失败（缺陷态——动作标注按成交对齐推导）"}
      </p>
      {exec.trades.length === 0 ? (
        <p className="py-3 text-center text-text-muted">零成交（该份无逐笔记录）</p>
      ) : (
        <div className="max-h-56 overflow-y-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-bg-base">
              <tr className="border-b border-border-default text-left text-[10px] text-text-muted">
                <th className="py-1">入场时间</th><th>seq</th><th>方向</th>
                <th>开价</th><th>平价</th><th>数量</th><th>盈亏</th><th>出场（决策信号）</th>
              </tr>
            </thead>
            <tbody>
              {exec.trades.map((t) => {
                const anchors = t.exit_time
                  ? exitAnchors.get(t.exit_time.slice(0, 16)) ?? []
                  : [];
                const exitFamily = anchors.some((a) => eventSemantics(a.event_code)?.family === "risk")
                  ? "risk"
                  : anchors.length > 0
                    ? "strategy"
                    : null;
                return (
                  <tr
                    key={t.seq}
                    className="cursor-pointer border-b border-border-faint hover:bg-white/5"
                    onClick={() => onLocate(t.entry_time)}
                    data-testid={`vr-trade-row-${t.seq}`}
                  >
                    <td className="py-1 font-num text-text-muted">{fmtTime(t.entry_time)}</td>
                    <td className="font-num text-text-muted">{t.seq}</td>
                    <td className="text-text-secondary">{t.direction === "short" ? "空" : "多"}</td>
                    <td className="font-num text-text-secondary">{t.entry_price ?? "—"}</td>
                    <td className="font-num text-text-secondary">
                      {t.exit_time ? (t.exit_price ?? "—") : "未平仓"}
                    </td>
                    <td className="font-num text-text-muted">{t.size ?? "—"}</td>
                    <td className={`font-num ${(t.pnl ?? 0) >= 0 ? "text-profit" : "text-loss"}`}>
                      {t.pnl != null ? t.pnl.toFixed(1) : "—"}
                    </td>
                    <td>
                      {anchors.length > 0 ? (
                        <span className="flex items-center gap-1">
                          <span
                            className="inline-block h-2 w-2 rounded-full"
                            style={{
                              backgroundColor: exitFamily
                                ? TRACE_FAMILY_COLOR[exitFamily]
                                : undefined,
                            }}
                          />
                          {anchors.map((a) => eventSemantics(a.event_code)?.label).join("·")}
                        </span>
                      ) : t.exit_time ? (
                        <span className="text-text-muted">无对齐留痕</span>
                      ) : (
                        <span className="text-text-muted">持仓中</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── V3 信号回溯（四元判读+reason 五值两族+capped+MTF 门控提示行） ── */
function SignalTrace({
  exec,
  detail,
}: {
  exec: VrExecution;
  detail: ValidationDetailResponse;
}) {
  // 四元判读：true+有行=渲染 / true+全份空=W0 缺陷 / null=代际陈述 /
  // false=管线失败缺陷（均非报错非隐藏——空态如实呈现）
  const st = detail.signal_trace;
  const rows = exec.trace_rows;
  let body: React.ReactNode;
  if (st === true && rows.length > 0) {
    body = (
      <div className="max-h-56 overflow-y-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-bg-base">
            <tr className="border-b border-border-default text-left text-[10px] text-text-muted">
              <th className="py-1">时间</th><th>seq</th><th>结果</th><th>原因</th>
              <th>动作</th><th>价格</th><th>数量</th><th>截断</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const sem = eventSemantics(r.event_code);
              const family = sem?.family ?? (r.outcome === "filled" ? "strategy" : "blocked");
              return (
                <tr key={`${r.bar_time}-${r.seq}-${i}`} className="border-b border-border-faint">
                  <td className="py-1 font-num text-text-muted">{fmtTime(r.bar_time)}</td>
                  <td className="font-num text-text-muted">{r.seq}</td>
                  <td>
                    <span className="flex items-center gap-1">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: TRACE_FAMILY_COLOR[family] }}
                      />
                      {OUTCOME_LABELS[r.outcome]}
                    </span>
                  </td>
                  <td className="text-text-secondary">
                    {r.reason ? (REASON_LABELS[r.reason] ?? r.reason) : "—"}
                  </td>
                  <td className="text-text-secondary">{sem?.label ?? "—"}</td>
                  <td className="font-num text-text-secondary">{r.price ?? "—"}</td>
                  <td className="font-num text-text-muted">{r.size ?? "—"}</td>
                  <td className="font-num text-attention-gold">
                    {r.capped != null ? `${(r.capped * 100).toFixed(0)}%` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  } else if (st === true) {
    body = (
      <p className="rounded border border-loss/40 px-2 py-1 text-[10px] text-loss" data-testid="vr-trace-w0">
        能力标记为已执行但该份无留痕行（缺陷态 W0）· {detail.record_id}
      </p>
    );
  } else if (st === null) {
    body = (
      <p className="text-[10px] text-text-muted" data-testid="vr-trace-legacy">
        该记录产生于信号留痕能力上线前——无信号回溯数据（代际说明）
      </p>
    );
  } else {
    body = (
      <p className="rounded border border-loss/40 px-2 py-1 text-[10px] text-loss" data-testid="vr-trace-failed">
        信号留痕管线执行失败（缺陷态 W0）· {detail.record_id}
      </p>
    );
  }
  return (
    <div className="text-xs" data-testid="vr-signal-trace">
      {/* MTF 门控提示行（null=无门控面与 0=全过不打扰——>0 才提示） */}
      {detail.mtf_gated_bars != null && detail.mtf_gated_bars > 0 && (
        <p className="mb-1 text-[10px] text-attention-gold" data-testid="vr-mtf-gated-note">
          MTF 门控拦截 {detail.mtf_gated_bars} bar——高周期条件未满足的 bar 不产生信号
        </p>
      )}
      <p className="mb-1 text-[10px] text-text-muted">
        蓝=策略信号 · 金=风控出场族 · 灰=未遂族（被拒/吞没）——reason 五值两族映射
      </p>
      {body}
    </div>
  );
}

/* ── V1 决策记录三页签（结构驱动恒三——权益明细页签与 K 线/副图严格同锚） ── */
function EquityPointsTable({ exec, bars }: { exec: VrExecution; bars: FullRangeBar[] }) {
  const pts = perExecEquityPoints(exec, bars);
  if (pts.length === 0) {
    return <p className="py-3 text-center text-xs text-text-muted">该份无权益明细</p>;
  }
  return (
    <div className="max-h-56 overflow-y-auto text-xs" data-testid="vr-equity-points">
      <table className="w-full">
        <thead className="sticky top-0 bg-bg-base">
          <tr className="border-b border-border-default text-left text-[10px] text-text-muted">
            <th className="py-1">时间</th><th>权益</th>
          </tr>
        </thead>
        <tbody>
          {pts.map((p, i) => (
            <tr key={`${p.time}-${i}`} className="border-b border-border-faint">
              <td className="py-1 font-num text-text-muted">{fmtTime(p.time)}</td>
              <td className="font-num text-text-secondary">{p.value.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** V-D 详情区四形态（D1 参数回显｜D2 K 线复核｜D3 资金曲线｜V1 决策
 *  记录三页签；detail_available=false 空骨架如实） */
export function RecordDetail({ record }: { record?: ValidationRecordRow }) {
  const recordId = record?.record_id;
  const q = useQuery({
    queryKey: ["validation-record-detail", recordId],
    queryFn: () => getValidationRecordDetail(recordId!),
    enabled: !!recordId,
  });
  // 视图状态（契约 E）：缺省全区间主视图；单份=前端过滤可选视图。
  // D3/V1 消费当前聚焦份（全区间态缺省份 1）
  const [view, setView] = useState<ViewSel>("all");
  // 视图周期（契约 K）：null=基准（执行语义锚）；无数据档禁用集（请求后降级）
  const [viewTf, setViewTf] = useState<string | null>(null);
  const [deadTfs, setDeadTfs] = useState<ReadonlySet<string>>(new Set());
  // REQ-4 重试序号（per-tf）与失败档集合（D5.3 重切失败档必重发——
  // queryKey 只携当前档序号，他档重试不动基准档缓存身份；失败标记
  // 集合化，多档先后失败各留标记互不覆盖——沿用 deadTfs 形制）
  const [failedTfs, setFailedTfs] = useState<ReadonlySet<string>>(new Set());
  const [retrySeqs, setRetrySeqs] = useState<Record<string, number>>({});
  const chartRef = useRef<KlineChartHandle | null>(null);
  const locate = (time: string) => chartRef.current?.scrollToTime(time);
  // 全区间装载（hooks 无条件区——早退前调用；明细未就绪/不可用时空
  // executions 恒 loading 态，bounds null 不发请求）；viewTf 维度=契约 K
  // 切换查看粒度（start/end 不变）
  const st = record ? recordSymbolTimeframe(record) : { symbol: null, timeframe: null };
  const fr = useFullRangeKline(
    q.data?.detail_available === true ? q.data.executions : [],
    st.symbol,
    st.timeframe,
    viewTf,
    retrySeqs,
  );
  // REQ-4：重切非 404 失败档时递增该档序号（queryKey 携序号强制重发）
  const handleViewTf = (tf: string | null) => {
    setViewTf(tf);
    if (tf !== null && failedTfs.has(tf))
      setRetrySeqs((prev) => ({ ...prev, [tf]: (prev[tf] ?? 0) + 1 }));
  };
  // 契约 K 判死分流（REQ-4/D5——render 期前值推导消解 set-state-in-effect）：
  // 404=档位无数据（禁用+回基准，零额外请求）；非 404=暂态失败（不入
  // deadTfs+回基准+提示条可见+重切该档必重发）。judgedError 引用稳定
  // （react-query 对同一失败持有同一 error 引用）防渲染循环
  const [judgedError, setJudgedError] = useState<unknown>(null);
  if (fr.state === "error" && viewTf != null && judgedError !== fr.error) {
    setJudgedError(fr.error);
    const is404 = fr.error instanceof ApiError && fr.error.status === 404;
    if (is404) {
      setDeadTfs((prev) => new Set(prev).add(viewTf));
    } else {
      setFailedTfs((prev) => new Set(prev).add(viewTf));
    }
    setViewTf(null);
  }
  // 非 404 暂态失败提示条（渲染期派生；该档装载即清除失败标记——
  // ok/truncated/mismatch 三态同分支携带 candles 照画，数据在场即非失败，
  // 否则截断/对账不等档重试成功后「失败可重试」提示条与已装载数据同屏矛盾）
  if (
    viewTf != null &&
    failedTfs.has(viewTf) &&
    fr.state !== "error" &&
    fr.state !== "loading"
  ) {
    // 用户重切失败档且该档查询装载（含截断/对账不等告警态）→ 移除该档标记
    setFailedTfs((prev) => {
      const next = new Set(prev);
      next.delete(viewTf);
      return next;
    });
  }
  const strategyId = q.data?.detail_available === true ? q.data.strategy_id : null;
  const strat = useQuery({ ...useStrategy(strategyId ?? ""), enabled: !!strategyId });
  const dnaExtract = useMemo(
    () => dnaIndicators((strat.data as { dna?: unknown } | undefined)?.dna),
    [strat.data],
  );

  if (!record || !recordId) {
    return (
      <p className="flex items-center gap-2 text-xs text-text-muted">
        <FileSearch className="h-3.5 w-3.5" /> 选择左侧任一验证记录 → 按需加载明细复核
      </p>
    );
  }
  if (q.isError) return <p className="text-xs text-loss">详情源暂不可达（稍后自动重试）</p>;
  if (q.isLoading) return <p className="text-xs text-text-muted">装载中…</p>;
  const d: ValidationDetailResponse = q.data!;
  if (!d.detail_available) {
    return (
      <p className="text-xs text-text-muted">
        该记录为历史无留痕形态（detail_available=false）——不可复核，空骨架如实呈现
      </p>
    );
  }
  const { symbol, timeframe } = st;
  const bars: FullRangeBar[] = fr.state === "loading" || fr.state === "error" ? [] : fr.candles;
  const activeExecIdx = view === "all" ? 0 : Math.min(view, d.executions.length - 1);
  const exec = d.executions[activeExecIdx];
  return (
    <div className="space-y-3">
      {/* 头部：标识+verdict+V4 三布尔能力徽标 */}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-text-secondary">{record.record_id}</span>
        <span className={VERDICT_COLOR[d.verdict] ?? ""}>{d.verdict}</span>
        <span className="text-text-muted">{d.level}</span>
      </div>
      <CapabilityBadges d={d} />
      {/* D1 参数回显两行 */}
      <ParamsEcho record={record} detail={d} />
      {d.executions.length > 1 && (
        <Select
          size="small"
          className="!w-64"
          value={view}
          onChange={(v) => setView(v)}
          data-testid="vr-view-select"
          options={[
            { value: "all", label: "全区间（全部份连续）" },
            ...d.executions.map((e, i) => ({
              value: i,
              label: `份 ${i + 1} · ${e.exec_ref}（${e.range_start || "?"} → ${e.range_end || "?"}）`,
            })),
          ]}
        />
      )}
      {exec ? (
        <>
          {/* D2 K 线复核（全区间单图） */}
          {/* REQ-4：非 404 暂态失败提示条（逐档一行，重切该档可重试） */}
          {[...failedTfs].map((tf) => (
            <p key={tf} className="text-xs text-loss" data-testid="vr-tf-fetch-failed">
              {tf} 档拉取失败（网络/服务异常），可重试——重切该档位即可
            </p>
          ))}
          <KlineReview
            executions={d.executions}
            fr={fr}
            view={view}
            symbol={symbol}
            timeframe={timeframe}
            stopPrice={d.stop_price}
            chartRef={chartRef}
            extract={dnaExtract}
            hasStrategy={!!strategyId}
            viewTf={viewTf}
            deadTfs={deadTfs}
            onViewTf={handleViewTf}
            recordDirection={
              typeof record.result?.direction === "string" ? record.result.direction : undefined
            }
          />
          {/* D3 资金曲线 */}
          <EquityReview exec={exec} bars={bars} record={record} />
          {/* V1 决策记录三页签（结构驱动恒三） */}
          <Tabs
            size="small"
            items={[
              {
                key: "trades",
                label: "成交记录",
                children: (
                  <TradeTable exec={exec} tradeSemantics={d.trade_semantics} onLocate={locate} />
                ),
              },
              {
                key: "trace",
                label: "信号回溯",
                children: <SignalTrace exec={exec} detail={d} />,
              },
              {
                key: "equity-points",
                label: "权益明细",
                children: <EquityPointsTable exec={exec} bars={bars} />,
              },
            ]}
          />
        </>
      ) : (
        <p className="text-xs text-text-muted">无执行份</p>
      )}
    </div>
  );
}
