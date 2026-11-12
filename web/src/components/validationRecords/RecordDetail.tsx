/**
 * 验证记录明细复核四形态（upgrade-validation-records-list 5.2——
 * D1-D3/V1-V4）。D1 参数回显两行（statement 全文逐字+union-balance
 * 余量——各自数据源不可达各自降级）；D2 K 线四类动作标记+止损线
 * （trail 档取 trail_series 真值禁重算；标记/逐笔/点行定位统一成交锚）；
 * D3 资金曲线+一致性核对行（isolated 恒等断言、random/staged worst
 * 窗口/最弱段统计身份标签）；V1 三页签结构驱动恒三；V2 逐笔表（开平
 * 双价/未平仓如实/色点单源三视图同色）；V3 信号回溯（四元判读+reason
 * 五值两族+capped 披露+MTF 门控提示行）；V4 三布尔独立降级（null=
 * 代际陈述中性小标、缺陷态异常带 record_id）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Select, Tabs } from "antd";
import { FileSearch } from "lucide-react";
import { KlineChart, type KlineChartHandle } from "@/components/charts/KlineChart";
import { CHART_VC } from "@/theme/tokens";
import { getOhlcvBySymbol } from "@/services/datasets";
import {
  getUnionBalance,
  getValidationRecordDetail,
  type ValidationDetailResponse,
  type ValidationRecordRow,
  type VrExecution,
  type VrTraceRow,
} from "@/services/validationRecords";
import {
  OUTCOME_LABELS,
  REASON_LABELS,
  TRACE_FAMILY_COLOR,
  eventSemantics,
  recordSymbolTimeframe,
} from "./traceSemantics";

const VERDICT_COLOR: Record<string, string> = {
  pass: "text-profit", fail: "text-loss", inconclusive: "text-attention-gold",
};

/** 周期字符串 → 秒数（compact 时间轴重建用；全档覆盖数据目录档位） */
function timeframeSeconds(tf: string): number {
  const m: Record<string, number> = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "3d": 259200,
  };
  return m[tf] ?? 0;
}

/** equity values → 时间轴点对（compact 按 start+length×tf 重建） */
function equityPoints(exec: VrExecution): Array<{ time: string; value: number }> {
  const eq = exec.equity;
  if (!eq?.values?.length) return [];
  if (eq.timestamps && eq.timestamps.length === eq.values.length) {
    return eq.timestamps.map((t, i) => ({ time: t, value: eq.values[i] }));
  }
  const sec = timeframeSeconds(eq.timeframe);
  if (!eq.start || !sec) return eq.values.map((_, i) => ({ time: String(i), value: eq.values[i] }));
  const startMs = new Date(eq.start.replace(" ", "T") + (eq.start.includes("+") ? "" : "Z")).getTime();
  if (!Number.isFinite(startMs)) return eq.values.map((_, i) => ({ time: String(i), value: eq.values[i] }));
  return eq.values.map((v, i) => ({
    time: new Date(startMs + i * sec * 1000).toISOString(),
    value: v,
  }));
}

const fmtTime = (t?: string | null) => (t ? t.slice(0, 16).replace("T", " ") : "—");
const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
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

/* ── D2 K 线复核（四类动作标记+止损线；trail 真值；统一成交锚） ── */
function KlineReview({
  exec,
  symbol,
  timeframe,
  stopPrice,
  chartRef,
}: {
  exec: VrExecution;
  symbol: string | null;
  timeframe: string | null;
  stopPrice: boolean | null;
  chartRef: React.RefObject<KlineChartHandle | null>;
}) {
  const q = useQuery({
    queryKey: ["vr-kline", symbol, timeframe, exec.range_start, exec.range_end],
    queryFn: () =>
      getOhlcvBySymbol(symbol!, timeframe!, {
        start: exec.range_start || undefined,
        end: exec.range_end || undefined,
      }),
    enabled: !!symbol && !!timeframe,
  });
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
  // trail 止损线：trail_series 真值挂线（引擎单源——禁前端重算）；
  // NaN/undefined 段传 null 由原语层断线。依赖含 q.isSuccess——K 线
  // 装载完成才渲染图表（ref 就绪晚于本 effect 首跑，装载完成信号驱动
  // 重挂线）
  const trail = exec.trail_series ?? null;
  useEffect(() => {
    const chart = chartRef.current;
    chart?.setObservationSeries(
      trail ? trail.map((p) => ({ time: p.time, value: p.value ?? null })) : null,
    );
    return () => chart?.setObservationSeries(null);
  }, [trail, chartRef, q.isSuccess]);
  const markers = useMemo(
    () =>
      exec.trace_rows
        .filter((r) => r.outcome === "filled")
        .flatMap((r) => {
          const sem = eventSemantics(r.event_code);
          if (!sem) return [];
          return [{
            time: r.bar_time,
            position: (sem.action === "open" || sem.action === "add"
              ? "belowBar"
              : "aboveBar") as "belowBar" | "aboveBar",
            color: TRACE_FAMILY_COLOR[sem.family],
            shape: (sem.action === "open"
              ? "arrowUp"
              : sem.action === "exit"
                ? "arrowDown"
                : "circle") as "arrowUp" | "arrowDown" | "circle",
            text: sem.markerText,
          }];
        }),
    [exec.trace_rows],
  );

  if (!symbol || !timeframe) {
    return (
      <p className="text-xs text-text-muted">
        行 params 无 symbol/timeframe——K 线区间不可定
      </p>
    );
  }
  if (q.isError) return <p className="text-xs text-loss">K 线源暂不可达（稍后自动重试）</p>;
  if (q.isLoading) return <p className="text-xs text-text-muted">K 线装载中…</p>;
  const candles = q.data?.data ?? [];
  const filled = exec.trace_rows.filter((r) => r.outcome === "filled");
  return (
    <div
      className="rounded p-2"
      style={{ backgroundColor: CHART_VC.bg, border: `1px solid ${CHART_VC.teal}22` }}
      data-testid="vr-kline-review"
    >
      <p className="mb-1 text-[10px]" style={{ color: CHART_VC.dim }}>
        {symbol} · {timeframe} · {exec.range_start || "?"} → {exec.range_end || "?"} ·
        标记 {markers.length}（filled 留痕 {filled.length} 行 · trades {exec.trades.length} 笔）
      </p>
      <p className="mb-1 flex flex-wrap gap-x-3 text-[10px]" style={{ color: CHART_VC.dim }}>
        <span style={{ color: TRACE_FAMILY_COLOR.strategy }}>▲开多/▲加多/▽减仓/▼平多=策略动作</span>
        <span style={{ color: TRACE_FAMILY_COLOR.risk }}>▼平多（金）=风控出场族</span>
        <span>止损线{trail ? "（trail 真值）" : stopPrice === null ? "（产生于能力上线前）" : "（非 trail 档无序列）"}</span>
      </p>
      {candles.length === 0 ? (
        <p className="py-6 text-center text-xs" style={{ color: CHART_VC.dim }}>
          该区间无 K 线数据
        </p>
      ) : (
        <KlineChart data={candles} signalMarkers={markers} height={chartHeight} ref={chartRef} />
      )}
    </div>
  );
}

/* ── D3 资金曲线+一致性核对行 ── */
function EquityReview({
  exec,
  record,
}: {
  exec: VrExecution;
  record: ValidationRecordRow;
}) {
  const pts = equityPoints(exec);
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
      consistency = { text: `与声称收益一致性核对：终值 ${fmtPct(claimed)}`, ok };
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

/* ── V1 决策记录三页签（结构驱动恒三——权益明细页签） ── */
function EquityPointsTable({ exec }: { exec: VrExecution }) {
  const pts = equityPoints(exec);
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
  // 多份选择器（隔离验证单份常态；多份=random 各窗/staged 各段——份
  // 标识 exec_ref 与段清单逐份一致）
  const [execIdx, setExecIdx] = useState(0);
  useEffect(() => { setExecIdx(0); }, [recordId]);
  const chartRef = useRef<KlineChartHandle | null>(null);
  const locate = (time: string) => chartRef.current?.scrollToTime(time);

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
  const { symbol, timeframe } = recordSymbolTimeframe(record);
  const exec = d.executions[Math.min(execIdx, d.executions.length - 1)];
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
          value={Math.min(execIdx, d.executions.length - 1)}
          onChange={setExecIdx}
          options={d.executions.map((e, i) => ({
            value: i,
            label: `份 ${i + 1} · ${e.exec_ref}（${e.range_start || "?"} → ${e.range_end || "?"}）`,
          }))}
        />
      )}
      {exec ? (
        <>
          {/* D2 K 线复核 */}
          <KlineReview
            exec={exec}
            symbol={symbol}
            timeframe={timeframe}
            stopPrice={d.stop_price}
            chartRef={chartRef}
          />
          {/* D3 资金曲线 */}
          <EquityReview exec={exec} record={record} />
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
                children: <EquityPointsTable exec={exec} />,
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
