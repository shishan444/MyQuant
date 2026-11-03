/**
 * 07 模拟交易·图表 HUD 四浮层（fe-asset-data-domain 2.1——K 线规格板
 * va 变体：va-bg 底+va-panel 浮层+va-amber 强调+va-dim 辅文）。
 * 四浮层=标题（标的·周期·最新价）/仓位（方向·数量·开仓价·浮盈亏）/
 * 信号（最近信号标记）/成交（最近成交回执）——数据全来自既有 paper
 * 端点真值（task 单查+fills+signals），空态如实「暂无」。
 * 叠加定位=图表容器左上角堆叠（绝对定位，不占布局流）；
 * ChartEmbeddedLegend 分组折叠能力保留在 KlineChart 内建（本 HUD 承载
 * 07 页浮层信息面）。消费方：Trading 详情卡。
 */
import { CHART_VA, POLARITY } from "@/theme/tokens";
import type { PaperFill, PaperSignal } from "@/types/api";

interface ChartHudProps {
  symbol: string;
  timeframe: string;
  /** 最新价（K 线末根 close；缺=null 呈「—」） */
  lastPrice: number | null;
  /** 仓位四要素（task 单查字段；空仓时 direction=null） */
  position: {
    side: string | null;
    quantity: number | null;
    entryPrice: number | null;
    upnl: number | null;
  } | null;
  /** 最近信号（倒序首条起呈现，至多 3） */
  signals: PaperSignal[];
  /** 最近成交（倒序首条起呈现，至多 2） */
  fills: PaperFill[];
}

/** 单浮层容器（va-panel 底+微透明+琥珀标题行） */
function HudCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="rounded px-2 py-1.5"
      style={{ backgroundColor: `${CHART_VA.panel}EE`, border: `1px solid ${CHART_VA.line}` }}
    >
      <p className="mb-0.5 text-[10px] font-medium" style={{ color: CHART_VA.amber }}>
        {title}
      </p>
      <div className="flex flex-col gap-0.5" style={{ color: CHART_VA.text }}>
        {children}
      </div>
    </div>
  );
}

function fmt(v: number | null | undefined, digits = 2): string {
  return v == null ? "—" : v.toFixed(digits);
}

const KIND_LABEL: Record<string, string> = {
  open: "开仓", close: "平仓", add: "加仓", reduce: "减仓",
  exit_sl: "止损出场", exit_tp: "止盈出场", exit_max_loss: "最大亏损出场",
  exit_max_hold: "超时出场", exit_time: "定时出场", exit_liquidation: "强平",
};

export function ChartHud({
  symbol,
  timeframe,
  lastPrice,
  position,
  signals,
  fills,
}: ChartHudProps) {
  const sideLabel =
    position?.side == null || position.side === "flat"
      ? "空仓"
      : position.side === "long"
        ? "做多"
        : position.side === "short"
          ? "做空"
          : position.side;
  return (
    <div
      className="pointer-events-none absolute left-2 top-2 z-10 flex w-44 flex-col gap-1"
      data-testid="chart-hud"
      style={{ backgroundColor: `${CHART_VA.bg}CC` }}
    >
      <HudCard title={`${symbol} · ${timeframe.toUpperCase()}`}>
        <span className="font-num text-xs" style={{ color: CHART_VA.amber }}>
          最新 {fmt(lastPrice)}
        </span>
      </HudCard>

      <HudCard title="仓位">
        <span className="font-num text-[11px]">
          {sideLabel} · 数量 {fmt(position?.quantity, 6)}
        </span>
        <span className="font-num text-[11px]">
          开仓 {fmt(position?.entryPrice)} ·{" "}
          <span style={{ color: (position?.upnl ?? 0) >= 0 ? CHART_VA.amber : POLARITY.down }}>
            浮盈亏 {fmt(position?.upnl)}
          </span>
        </span>
      </HudCard>

      <HudCard title="信号">
        {signals.length === 0 ? (
          <span className="text-[11px]" style={{ color: CHART_VA.dim }}>暂无信号记录</span>
        ) : (
          signals.slice(0, 3).map((s) => (
            <span key={`${s.bar_time}-${s.seq}`} className="truncate font-num text-[11px]" title={`${s.bar_time} seq=${s.seq}`}>
              {KIND_LABEL[s.signal_kind] ?? s.signal_kind} · {s.outcome}
              {s.signal_kind === "open" || s.signal_kind === "close" ? "" : ` · ${s.bar_time.slice(5, 16)}`}
            </span>
          ))
        )}
      </HudCard>

      <HudCard title="成交">
        {fills.length === 0 ? (
          <span className="text-[11px]" style={{ color: CHART_VA.dim }}>暂无成交</span>
        ) : (
          fills.slice(0, 2).map((f) => (
            <span key={f.id} className="truncate font-num text-[11px]" title={`${f.bar_time} ${f.reason ?? ""}`}>
              {KIND_LABEL[f.kind] ?? f.kind} {fmt(f.price)} × {fmt(f.quantity, 6)}
            </span>
          ))
        )}
      </HudCard>
    </div>
  );
}
