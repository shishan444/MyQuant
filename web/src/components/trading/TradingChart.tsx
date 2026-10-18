import { useMemo, useState } from "react";
import { KlineChart } from "@/components/charts/KlineChart";
import { useChartIndicators, type SubChartType } from "@/hooks/useChartIndicators";
import type { PaperFill } from "@/services/trading";
import type { SignalData } from "@/components/charts/KlineChart";

interface TradingChartProps {
  symbol: string;
  timeframe: string;
  fills: PaperFill[];
  height?: number;
  isActive?: boolean;
}

/** timeframe string -> minutes */
const TF_MINUTES: Record<string, number> = {
  "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
  "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
  "1d": 1440, "3d": 4320, "1w": 10080,
};

const SUB_CHART_OPTIONS: { value: SubChartType; label: string }[] = [
  { value: "volume", label: "VOL" },
  { value: "macd", label: "MACD" },
  { value: "rsi", label: "RSI" },
  { value: "kdj", label: "KDJ" },
];

/** Calculate start date for the last N bars based on timeframe. */
function barsAgoDate(timeframe: string, bars: number): string {
  const minutes = (TF_MINUTES[timeframe] ?? 240) * bars;
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

/** fill.kind 出场五值归并为 close（KlineChart 信号动作词汇四值） */
const EXIT_KINDS = new Set(["exit_sl", "exit_tp", "exit_max_hold", "exit_time", "exit_liquidation"]);

function fillAction(kind: string): "open" | "close" | "add" | "reduce" {
  return EXIT_KINDS.has(kind) ? "close" : (kind as "open" | "close" | "add" | "reduce");
}

function fillsToSignals(fills: PaperFill[]): SignalData[] {
  return fills
    .filter((f) => f.side) // 出场全平类 side=null 不画信号（与旧口径一致）
    .map((f) => ({
      type: `${fillAction(f.kind)}_${f.side}` as SignalData["type"],
      timestamp: f.bar_time,
    }));
}

export function TradingChart({ symbol, timeframe, fills, height = 420, isActive }: TradingChartProps) {
  const [subChartType, setSubChartType] = useState<SubChartType>("volume");

  // Only load the last 200 bars of data for paper trading chart
  const dateRange = useMemo(() => ({ start: barsAgoDate(timeframe, 200) }), [timeframe]);

  const {
    candleData,
    volumeData,
    chartIndicators,
    chartBollData,
    macdData,
    kdjData,
    isLoadingOhlcv,
  } = useChartIndicators({
    symbol,
    timeframe,
    dateRange,
    subChartType,
    refetchInterval: isActive ? 30_000 : false,
  });

  const signals = useMemo(() => fillsToSignals(fills), [fills]);

  if (isLoadingOhlcv || !candleData?.length) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-border-default"
        style={{ height }}>
        <span className="text-sm text-text-muted">Loading chart...</span>
      </div>
    );
  }

  return (
    <div>
      {/* Sub-chart indicator selector */}
      <div className="flex items-center gap-0.5 mb-1">
        <span className="mr-1 text-[11px] text-slate-500">Sub-chart</span>
        {SUB_CHART_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => setSubChartType(opt.value)}
            className={`rounded px-1.5 py-0.5 text-[11px] transition-colors ${
              subChartType === opt.value
                ? "bg-sky-400/20 text-sky-400"
                : "bg-slate-800/30 text-slate-500 hover:text-slate-400"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <KlineChart
        data={candleData}
        signals={signals}
        indicators={chartIndicators}
        bollData={chartBollData}
        macdData={macdData}
        kdjData={kdjData}
        volumeData={volumeData}
        subChartType={subChartType}
        height={height}
      />
    </div>
  );
}
