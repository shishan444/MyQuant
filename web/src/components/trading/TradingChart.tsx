import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { KlineChart } from "@/components/charts/KlineChart";
import type { KlineChartHandle } from "@/components/charts/KlineChart";
import { useChartIndicators, type SubChartType } from "@/hooks/useChartIndicators";
import { SymbolInfoBar } from "@/components/trading/SymbolInfoBar";
import { fillsToSignals } from "@/components/charts/primitives";
import type { PaperFill } from "@/services/trading";
import type { SignalData } from "@/components/charts/KlineChart";

interface TradingChartProps {
  symbol: string;
  timeframe: string;
  fills: PaperFill[];
  height?: number;
  isActive?: boolean;
  /** 任务累计资金费（信息栏直读，不传则不呈现该字段） */
  cumulativeFunding?: number | null;
  /** 当前止损价（标量水平线——pt_task.position_sl_price） */
  stopLossPrice?: number | null;
  /** trail 观测序列（pt_equity.trail_sl_price 逐 bar——重演窗口移动线） */
  trailSeries?: Array<{ bar_time: string; trail_sl_price: number | null }>;
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

/** 按 timeframe 计算最近 N 根 bar 的起始日期。 */
function barsAgoDate(timeframe: string, bars: number): string {
  const minutes = (TF_MINUTES[timeframe] ?? 240) * bars;
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

export function TradingChart({ symbol, timeframe, fills, height = 470, isActive, cumulativeFunding,
  stopLossPrice, trailSeries }: TradingChartProps) {
  // F1 标注挂载（paper-trading-revamp）：当前止损价=标量水平线（annotations
  // 价线族，chart ready 单次挂——SL 价为标量随任务刷新，重挂通道待后续组）；
  // trail 序列经 KlineChartHandle.setObservationSeries 命令式通道挂线
  // （hypothesis-workbench 审查 CARD-04：切 attachObservationSeries 原语——
  // 30s 轮询 refetch 新 trail 数据后获得挂/重挂通道，原 onChartReady 单次
  // 挂线在数据更新后恒 stale）；形参类型化沿 CARD-13（SceneModePanel 先例）
  const handleChartReady = useCallback((_chart: IChartApi, candleSeries: ISeriesApi<"Candlestick">) => {
    if (stopLossPrice != null) {
      candleSeries.createPriceLine({
        price: stopLossPrice,
        color: "#FBBF24",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "SL(当前)",
      });
    }
  }, [stopLossPrice]);

  const klineRef = useRef<KlineChartHandle>(null);
  const trailPoints = useMemo(
    () => (trailSeries ?? []).map((t) => ({ time: t.bar_time, value: t.trail_sl_price })),
    [trailSeries]);
  useEffect(() => {
    klineRef.current?.setObservationSeries(trailPoints);
  }, [trailPoints]);

  const [subChartType, setSubChartType] = useState<SubChartType>("volume");

  // 模拟图表只装载最近 200 根数据
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

  // fills→signals 已收敛 primitives（CARD-04 双写归一）；受控断言=宽返回收窄到
  // KlineChart signals prop 的判别联合（子类型单向收窄）
  const signals = useMemo(() => fillsToSignals(fills) as SignalData[], [fills]);

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
      <SymbolInfoBar
        symbol={symbol}
        timeframe={timeframe}
        candles={candleData}
        cumulativeFunding={cumulativeFunding}
      />
      {/* 副图指标选择器 */}
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
        ref={klineRef}
        onChartReady={(chart, series) => {
          handleChartReady(chart, series);
          // trail 线重挂载重挂信号（KlineChart 重建时 effect 不重跑——
          // ref 赋值先于本回调，handle 恒就绪）
          klineRef.current?.setObservationSeries(trailPoints);
        }}
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
