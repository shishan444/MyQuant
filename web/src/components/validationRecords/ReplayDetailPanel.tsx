/**
 * 回放详情面板（fe-replay-presentation 任务 2.1/3.1/4.1）。
 *
 * level=replay 记录专属：明细数据源=纸盘明细端点（task_id 经
 * result.replay.task_id——P2 记录不带 vr 细节三表，pt_* 四明细经
 * 纸盘 REST 通道直取）；呈现=六态标记 K 线+仓位操作流水+明细↔图
 * 互达+基因触发描述（gene_hits）。
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { KlineChart } from "@/components/charts/KlineChart";
import { formatTimestamp } from "@/lib/utils";

/** 纸盘成交行（/tasks/{id}/fills——PtFill 列面子集） */
interface PaperFill {
  bar_time: string;
  seq: number;
  kind: string;
  side: string;
  price: number;
  quantity: number;
  fee: number;
  realized_pnl: number | null;
  reason: string | null;
}
/** 纸盘信号行（gene_hits 字段族——触发描述数据源） */
interface PaperSignal {
  bar_time: string;
  kind: string;
  outcome: string;
  fill_seq: number | null;
  reject_reason: string | null;
  gene_hits: Array<{
    indicator: string; field?: string;
    condition?: string; target?: number | string;
  }> | null;
}
interface ValidationRecordLike {
  record_id: string;
  level: string;
  result?: Record<string, unknown> | null;
}

const API = {
  fills: (tid: string) =>
    `/api/paper-trading/tasks/${encodeURIComponent(tid)}/fills?limit=10000`,
  signals: (tid: string) =>
    `/api/paper-trading/tasks/${encodeURIComponent(tid)}/signals?limit=10000`,
  ohlcv: (symbol: string, tf: string, start: string, end: string) =>
    `/api/data/datasets/${encodeURIComponent(`${symbol}_${tf}`)}/ohlcv` +
    `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&limit=50000`,
};

/** 六态标记映射（词表沿 fe-vr-marker-fidelity 六字义+价位锚定契约） */
export function replayMarker(f: PaperFill): {
  text: string;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  color: string;
  action: "open" | "add" | "reduce" | "exit";
} {
  const dirLong = f.side === "long" || f.side === "buy";
  const terminated = f.reason === "reduce_terminated";
  if (f.kind === "open") {
    return { text: dirLong ? "开多" : "开空",
      position: dirLong ? "belowBar" : "aboveBar",
      shape: dirLong ? "arrowUp" : "arrowDown", color: "#3b82f6",
      action: "open" };
  }
  if (f.kind === "add") {
    return { text: dirLong ? "加仓" : "加空",
      position: "belowBar", shape: "circle", color: "#8b5cf6",
      action: "add" };
  }
  if (f.kind === "reduce") {
    return { text: dirLong ? "减仓" : "减空",
      position: "aboveBar", shape: "circle", color: "#f59e0b",
      action: "reduce" };
  }
  // 出场族（close/exit_*——词表 else 分支承载）：平多/平空；
  // reduce_terminated 归平仓态且描述可辨识（流水行「减仓终止」）
  void terminated;
  return { text: dirLong ? "平多" : "平空",
    position: dirLong ? "aboveBar" : "belowBar",
    shape: dirLong ? "arrowDown" : "arrowUp", color: "#ef4444",
    action: "exit" };
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
    let qty = sign * f.quantity;
    if (f.kind === "open") { pos = qty; }
    else if (f.kind === "add") { pos += qty; }
    else if (f.kind === "reduce") {
      qty = -sign * f.quantity;  // 持仓方向内的减量（有符号取负）
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

/** 基因触发描述（任务 4.1）：gene_hits join 经 (bar_time, fill_seq) */
export function geneDescription(
  signals: PaperSignal[], barTime: string, fillSeq: number | null,
): string {
  const hit = signals.find(
    (s) => s.bar_time === barTime && s.fill_seq === fillSeq
      && s.gene_hits && s.gene_hits.length > 0);
  if (!hit) return "";
  return hit.gene_hits!.map(
    (g) => `${g.indicator}${g.field ? `.${g.field}` : ""} `
      + `${g.condition ?? ""} ${g.target ?? ""}`.trim(),
  ).join("；");
}

export function ReplayDetailPanel({ record }: { record: ValidationRecordLike }) {
  const replay = (record.result ?? {}) as {
    replay?: { task_id?: string; status?: string;
               total_trades?: number; total_pnl?: number };
  };
  const tid = replay.replay?.task_id;
  const [selected, setSelected] = useState<string | null>(null);

  const fillsQ = useQuery({
    queryKey: ["replay-fills", tid],
    queryFn: async (): Promise<PaperFill[]> => {
      if (!tid) return [];
      const r = await fetch(API.fills(tid));
      if (!r.ok) return [];
      const j = await r.json();
      return (j.fills ?? j.items ?? []) as PaperFill[];
    },
    enabled: !!tid,
  });
  const signalsQ = useQuery({
    queryKey: ["replay-signals", tid],
    queryFn: async (): Promise<PaperSignal[]> => {
      if (!tid) return [];
      const r = await fetch(API.signals(tid));
      if (!r.ok) return [];
      const j = await r.json();
      return (j.signals ?? j.items ?? []) as PaperSignal[];
    },
    enabled: !!tid,
  });

  const fills = fillsQ.data ?? [];
  const signals = signalsQ.data ?? [];
  const flow = useMemo(() => derivePositionFlow(fills), [fills]);
  const markers = useMemo(
    () => fills.map((f) => ({
      time: f.bar_time,
      price: f.price,
      ...replayMarker(f),
    })),
    [fills],
  );
  if (!tid) {
    return <p className="text-xs text-text-muted">回放结果缺 task_id 引用（旧形制记录）——不可复核</p>;
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
      {/* 六态标记 K 线（点位锚定沿 fe-vr-marker-fidelity 契约） */}
      {fillsQ.isLoading ? (
        <p className="text-xs text-text-muted">成交明细装载中…</p>
      ) : fills.length === 0 ? (
        <p className="text-xs text-text-muted">无成交（策略区间内零信号触发——空态如实）</p>
      ) : (
        <KlineChart
          data={[]}
          signalMarkers={markers}
          height={320}
          subChartType="volume"
        />
      )}
      {/* 仓位操作流水（任务 3.1——每笔一行：前/量/后/触发原因+基因描述） */}
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
    </div>
  );
}
