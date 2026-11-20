/**
 * 回放详情面板（fe-replay-presentation 任务 2.1/3.1/4.1）。
 *
 * level=replay 记录专属：明细数据源=纸盘明细端点（task_id 经
 * result.replay.task_id——P2 记录不带 vr 细节三表，pt_* 四明细经
 * 纸盘 REST 通道直取）；呈现=六态标记 K 线+仓位操作流水+明细↔图
 * 互达+基因触发描述（gene_hits）。
 */
import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { KlineChart } from "@/components/charts/KlineChart";
import { epochOf } from "./fullRangeData";
import { DETAIL_PAGE_SIZE, pagedFetch } from "./pagedFetch";
import { getTradingFills, getTradingSignals } from "@/services/trading";
import type { PaperSignal as PaperSignalDto } from "@/services/trading";
import {
  eventSemantics, legacySide, markerMorphText, markerShape,
  markerSide, normalizeDirection, TRACE_FAMILY_COLOR,
} from "./traceSemantics";
import { getOhlcvBySymbol } from "@/services/datasets";
import { formatTimestamp } from "@/lib/utils";

/** 纸盘成交行（/tasks/{id}/fills——服务层 PaperFill 呈现子集，
 *  可空形对齐生成类型：side/price/quantity/fee 后端 Optional 保守映射） */
interface PaperFill {
  bar_time: string;
  seq: number;
  kind: string;
  side?: string | null;
  price?: number | null;
  quantity?: number | null;
  fee?: number | null;
  realized_pnl?: number | null;
  reason?: string | null;
}
/** 纸盘信号行（gene_hits 字段族——触发描述数据源；服务层形经
 *  signalToPanel 收窄——kind 字段后端实为 signal_kind 且组件无消费，不设） */
interface PaperSignal {
  bar_time: string;
  /** 执行 bar（本根）——与 pt_fill.bar_time 同轴（C5 三元组锚
   *  task_id+exec_bar_time+fill_seq；bar_time=决策锚差一根） */
  exec_bar_time?: string | null;
  outcome: string;
  fill_seq?: number | null;
  reject_reason?: string | null;
  gene_hits: Array<{
    indicator: string; field?: string;
    condition?: string; target?: number | string;
  }> | null;
}
interface ValidationRecordLike {
  record_id: string;
  level: string;
  result?: Record<string, unknown> | null;
  /** P2 记录 params（列表通道已解析对象）：symbol/timeframe/start/end
   *  顶层四键是 K 线复核取数面（traceSemantics recordSymbolTimeframe
   *  同源识别形制） */
  params?: Record<string, unknown> | null;
}

/** 回放 fill kind → vr 事件码适配（P3-CARD-05 薄层）：词表/色/
 *  形/箭头全吃 traceSemantics EVENT_SEMANTICS 单源——六字义=开多/
 *  开空/加仓/减仓/平多/平空（加减仓无方向词缀，方向经侧位/价格
 *  就近承载——fe-vr-marker-fidelity 终态裁决）；close 族 reason
 *  （signal/reverse/reduce_terminated 升格）统一归 strategy 平仓
 *  态码 13（升格辨析由流水行「减仓终止」文案承载）；exit_max_hold
 *  补码 25（fix-replay-presentation-pipeline 2.1——vr 域 max_hold
 *  词条先例，pt_fill.kind 十值全覆盖）；词表未命中走 replayMarker
 *  未识别防线（如实标注不滤除） */
function replayEventCode(kind: string): number | null {
  if (kind === "open") return 10;
  if (kind === "add") return 11;
  if (kind === "reduce") return 12;
  if (kind === "exit_sl") return 20;
  if (kind === "exit_tp") return 21;
  if (kind === "exit_liquidation") return 22;
  if (kind === "exit_max_loss") return 23;
  if (kind === "exit_time") return 24;
  if (kind === "exit_max_hold") return 25;
  if (kind === "close") return 13;
  return null;
}

/** 六态标记映射（P3-CARD-05：薄适配层接 EVENT_SEMANTICS 单源——
 *  与 vr 标记同码同字义同色；position=legacySide 族侧位回退值，
 *  组装层有 bar 时按 markerSide 价格就近覆盖。未识别防线
 *  （fix-replay-presentation-pipeline 2.2）：词表未命中如实标注
 *  「未识别动作（kind 原文）」不返回 null 滤除——防未来 kind 扩
 *  值静默丢标记；灰族色+action 缺省（排序退化输入序——不猜动作族） */
export function replayMarker(f: PaperFill): {
  text: string;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  color: string;
  action?: "open" | "add" | "reduce" | "exit";
} {
  const code = replayEventCode(f.kind);
  const sem = code != null ? eventSemantics(code) : null;
  if (!sem) {
    return {
      text: `未识别动作（${f.kind}）`,
      position: "aboveBar",
      shape: "circle",
      color: TRACE_FAMILY_COLOR.blocked,
    };
  }
  const dir = normalizeDirection(f.side);
  return {
    text: markerMorphText(sem, dir),
    position: legacySide(sem.action),
    shape: markerShape(sem, dir),
    color: TRACE_FAMILY_COLOR[sem.family],
    action: sem.action,
  };
}

/** 仓位流水派生（纯函数——任务 3.1）：逐笔差分操作前/量/后仓位 */
export function derivePositionFlow(
  fills: PaperFill[],
): Array<{
  before: number; qty: number; after: number;
  dirLabel: string; reason: string;
}> {
  let pos = 0;
  return fills.map((f) => {
    const before = pos;
    const sign = f.side === "long" || f.side === "buy" ? 1 : -1;
    let qty = sign * (f.quantity ?? 0);  // 可空形守卫（null 量=零操作量）
    if (f.kind === "open") { pos = qty; }
    else if (f.kind === "add") { pos += qty; }
    else if (f.kind === "reduce") {
      qty = -sign * (f.quantity ?? 0);  // 持仓方向内的减量（有符号取负）
      pos += qty;
    } else { qty = -before; pos = 0; }  // 出场族=全量平（含终止升格）
    return {
      before, qty,
      after: pos,
      dirLabel: replayMarker(f).text,
      reason: f.reason ?? "signal",
    };
  });
}

/** 基因触发描述（任务 4.1）：gene_hits join 经 (exec_bar_time,
 *  fill_seq)——fill.bar_time=执行 bar 与 signal.exec_bar_time 同轴
 *  （P3-CARD-02：此前 join signal.bar_time 决策锚差一根恒 miss，
 *  pending 族〔八步序次根执行〕描述恒空串全族零收益；即时族
 *  同根同值两形皆正确命中） */
export function geneDescription(
  signals: PaperSignal[], barTime: string, fillSeq: number | null,
): string {
  const hit = signals.find(
    (s) => s.exec_bar_time === barTime && s.fill_seq === fillSeq
      && s.gene_hits && s.gene_hits.length > 0);
  if (!hit) return "";
  return hit.gene_hits!.map(
    (g) => `${g.indicator}${g.field ? `.${g.field}` : ""} `
      + `${g.condition ?? ""} ${g.target ?? ""}`.trim(),
  ).join("；");
}

/** 服务层信号行 → 呈现形：gene_hits 从生成层松散 dict（后端 List[dict]
 *  的保守 TS 映射）收窄为结构化呈现契约——indicator 等四键非字符串值
 *  如实串化/回退，不丢行（CARD-01 服务层收敛的类型边界收窄） */
function signalToPanel(s: PaperSignalDto): PaperSignal {
  return {
    ...s,
    gene_hits: s.gene_hits.map((g) => ({
      indicator: typeof g.indicator === "string"
        ? g.indicator : String(g.indicator ?? ""),
      field: typeof g.field === "string" ? g.field : undefined,
      condition: typeof g.condition === "string"
        ? g.condition : undefined,
      target: typeof g.target === "number" || typeof g.target === "string"
        ? g.target : undefined,
    })),
  };
}

export function ReplayDetailPanel({ record }: { record: ValidationRecordLike }) {
  const replay = (record.result ?? {}) as {
    replay?: { task_id?: string; status?: string;
               total_trades?: number; total_pnl?: number };
  };
  // task_id 形制闸（security F-P3-2）：拒路径语义字符（防 ../ 与 /
  // 经 URL 规范化跳段）——非白名单 12hex（服务端可注入任意串，
  // 12hex 会误拒注入形制——P3 审查对 security 建议的修正）；不匹
  // 配走下方「引用不可用」降级分支
  const TID_RE = /^[0-9a-zA-Z_-]{1,64}$/;
  const tidRaw = replay.replay?.task_id;
  const tid = tidRaw != null && TID_RE.test(tidRaw) ? tidRaw : undefined;
  const [selected, setSelected] = useState<string | null>(null);
  // 行 DOM ref（互达「标记→行」：scrollIntoView 定位面——key=行键）
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});

  // K 线复核取数面（P3-CARD-01）：params 顶层 symbol/timeframe/start/
  // end 四键（P2 记录形制；缺任一不发请求——回放区间即呈现区间）
  const st = useMemo(() => {
    const p = (record.params ?? {}) as Record<string, unknown>;
    const s = (k: string): string | null =>
      typeof p[k] === "string" ? (p[k] as string) : null;
    return { symbol: s("symbol"), timeframe: s("timeframe"),
            start: s("start"), end: s("end") };
  }, [record.params]);

  // 明细装载（fix-replay-presentation-pipeline 1.1/1.2）：分页聚
  //  合循环器（页大小=端点上限 500，按页满/has_more 续页聚合全量）
  //  ——limit=10000 恒 422+`!r.ok → []` 吞错伪装空态的病灶路径已
  //  删除；任页失败 queryFn 抛错走 isError 降级（不部分呈现）。
  //  单页取数经服务层单源（CARD-01：鉴权/基址/超时+signal 取消
  //  四能力继承——不再自拼 URL 自建裸 fetch 通道）
  const fillsQ = useQuery({
    queryKey: ["replay-fills", tid],
    queryFn: ({ signal }) => pagedFetch<PaperFill>({
      fetchPage: async (offset, pageSignal) => {
        const r = await getTradingFills(
          tid!, { limit: DETAIL_PAGE_SIZE, offset }, pageSignal);
        return { items: r.fills, hasMore: r.has_more };
      },
      signal,
    }),
    enabled: !!tid,
  });
  const signalsQ = useQuery({
    queryKey: ["replay-signals", tid],
    queryFn: ({ signal }) => pagedFetch<PaperSignal>({
      fetchPage: async (offset, pageSignal) => {
        const r = await getTradingSignals(
          tid!, { limit: DETAIL_PAGE_SIZE, offset }, pageSignal);
        return {
          items: r.signals.map(signalToPanel),
          hasMore: r.has_more,
        };
      },
      signal,
    }),
    enabled: !!tid,
  });
  // K 线主体（P3-CARD-01）：服务层单源（路径段编码+ApiError 错误
  // 通道），回放区间=呈现区间；isError 走降级文案（图不渲染不报错）
  const ohlcvQ = useQuery({
    queryKey: ["replay-ohlcv", st.symbol, st.timeframe, st.start, st.end],
    queryFn: async () => {
      const j = await getOhlcvBySymbol(
        st.symbol!, st.timeframe!,
        { start: st.start ?? undefined, end: st.end ?? undefined,
          limit: 50000 });
      return (j.data ?? []) as Array<{
        timestamp: string; open: number; high: number; low: number;
        close: number }>;
    },
    enabled: !!tid && st.symbol != null && st.timeframe != null
      && st.start != null && st.end != null,
  });

  const fills = fillsQ.data ?? [];
  const signals = signalsQ.data ?? [];
  const flow = useMemo(() => derivePositionFlow(fills), [fills]);
  // bar 中点索引（就近侧位判定面——RecordDetail 同构）
  const barByEpoch = useMemo(() => {
    const m = new Map<number, { high: number; low: number }>();
    for (const b of ohlcvQ.data ?? []) {
      const e = epochOf(b.timestamp);
      if (e != null) m.set(e, b);
    }
    return m;
  }, [ohlcvQ.data]);
  const markers = useMemo(
    () => fills.map((f) => {
      const mk = replayMarker(f);  // 未识别 kind 如实标注（不滤除）
      const e = epochOf(f.bar_time);
      const bar = e != null ? barByEpoch.get(e) : undefined;
      // 侧位（fe-vr-marker-fidelity 契约 4 同构）：有 bar 有价按
      // 价格就近（markerSide），否则族侧位回退（legacySide）
      const position = f.price != null && bar
        ? markerSide(f.price, bar) : mk.position;
      return { time: f.bar_time, price: f.price, ...mk, position };
    }),
    [fills, barByEpoch],
  );
  // 互达（P3-CARD-03）：行→图经 selectedMarkerTime（epoch 域——
  // epochOf 与 KlineChart toTime 同源对齐）；图→行经 onMarkerClick
  // 定位该 bar 首笔成交行（同 bar 多笔取首行）滚动+高亮
  const selectedEpoch = useMemo(() => {
    if (!selected) return null;
    return epochOf(selected.split("#")[0]);
  }, [selected]);
  const locateRow = (epoch: number) => {
    const f = fills.find((x) => epochOf(x.bar_time) === epoch);
    if (!f) return;
    const key = `${f.bar_time}#${f.seq}`;
    setSelected(key);
    rowRefs.current[key]?.scrollIntoView(
      { behavior: "smooth", block: "center" });
  };
  if (!tid) {
    return <p className="text-xs text-text-muted">回放结果缺 task_id 引用或形制不可用（旧形制/非法形制记录）——不可复核</p>;
  }
  return (
    <div className="space-y-3" data-testid="replay-detail-panel">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="font-mono text-text-secondary">{record.record_id}</span>
        <span className="text-text-muted">replay</span>
        {replay.replay?.status && (
          <span className="text-text-muted">{replay.replay.status}</span>)}
        <span className="text-text-muted">
          {replay.replay?.total_trades ?? 0} 笔
        </span>
      </div>
      {/* 六态标记 K 线（P3-CARD-01 补主体+P3-CARD-03 互达；点位锚定
          沿 fe-vr-marker-fidelity 契约——K 线=回放区间、六态标记挂
          真实 bar 时间轴；ohlcv 不可达降级文案如实不报错） */}
      {fillsQ.isLoading || signalsQ.isLoading ? (
        <p className="text-xs text-text-muted">成交明细装载中…</p>
      ) : fillsQ.isError || signalsQ.isError ? (
        <div className="space-y-1 text-xs" data-testid="replay-detail-error">
          <p className="text-text-muted">
            明细源不可达（成交/信号明细请求失败）——四件套降级，不呈现半量数据
          </p>
          <button
            type="button"
            className="rounded border border-border px-2 py-0.5"
            onClick={() => {
              void fillsQ.refetch();
              void signalsQ.refetch();
            }}
          >
            重试
          </button>
        </div>
      ) : (
        <>
          {fills.length === 0 && (
            <p className="text-xs text-text-muted">
              无成交（策略区间内零信号触发——空态如实）
            </p>
          )}
          {/* K 线主体链（P3-CARD-01）：与成交空态独立——零成交时
              主体仍在（区间数据在、标记空）；三降级态如实文案不报错 */}
          {ohlcvQ.isLoading ? (
            <p className="text-xs text-text-muted">K 线装载中…</p>
          ) : ohlcvQ.isError ? (
            <p className="text-xs text-text-muted">
              K 线数据源暂不可达（成交明细与标记通道不受影响）
            </p>
          ) : (ohlcvQ.data ?? []).length === 0 ? (
            <p className="text-xs text-text-muted">
              K 线主体不可得（记录缺 symbol/timeframe/start/end 或区间无数据）
            </p>
          ) : (
            <KlineChart
              data={ohlcvQ.data ?? []}
              signalMarkers={markers}
              height={320}
              subChartType="volume"
              selectedMarkerTime={selectedEpoch}
              onMarkerClick={locateRow}
            />
          )}
          {/* 仓位操作流水（任务 3.1——每笔一行：前/量/后/触发原因
              +基因描述；失败态整体降级——不呈现半量流水） */}
          <div className="text-xs">
            <p className="mb-1 font-medium">仓位操作流水（逐笔审计）</p>
            {flow.length === 0 ? (
              <p className="text-text-muted">无操作记录</p>
            ) : (
              <table className="w-full font-num text-[11px]" data-testid="replay-position-flow">
                <thead>
                  <tr className="text-text-muted">
                    <th className="text-left">时间</th><th className="text-left">动作</th>
                    <th className="text-right">前仓位</th><th className="text-right">操作量</th>
                    <th className="text-right">后仓位</th><th className="text-left">触发原因</th>
                  </tr>
                </thead>
                <tbody>
                  {fills.map((f, i) => {
                    const key = `${f.bar_time}#${f.seq}`;
                    const desc = geneDescription(signals, f.bar_time, f.seq);
                    return (
                      <tr
                        key={key}
                        ref={(el) => { rowRefs.current[key] = el; }}
                        onClick={() => setSelected(selected === key ? null : key)}
                        className={`cursor-pointer border-t border-border ${
                          selected === key ? "bg-surface-active" : ""}`}
                        data-testid={`replay-flow-row-${i}`}
                      >
                        <td>{formatTimestamp(f.bar_time, "minute")}</td>
                        <td>{flow[i].dirLabel}</td>
                        <td className="text-right">{flow[i].before.toFixed(4)}</td>
                        <td className="text-right">{flow[i].qty.toFixed(4)}</td>
                        <td className="text-right">{flow[i].after.toFixed(4)}</td>
                        <td>
                          {f.reason === "reduce_terminated" ? "减仓终止" : flow[i].reason}
                          {selected === key && desc && (
                            <span className="ml-2 text-text-muted">
                              基因：{desc}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
