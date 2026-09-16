import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useChartSettings } from "@/stores/chart-settings";
import { useShallow } from "zustand/react/shallow";
import { ohlcvOptions, chartIndicatorOptions } from "./queries/chartQueries";
import type { IndicatorData } from "@/components/charts/KlineChart";
import type { BollingerBandData } from "@/types/chart";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SubChartType = "volume" | "macd" | "rsi" | "kdj" | "equity";

export interface UseChartIndicatorsParams {
  symbol: string;
  timeframe: string;
  dateRange: { start?: string; end?: string };
  /** Which sub-chart indicator is active. Defaults to "volume". */
  subChartType?: SubChartType;
  /** Whether to run the queries. Defaults to true. */
  enabled?: boolean;
  /** Refetch interval in ms for OHLCV query. Defaults to false (no refetch). */
  refetchInterval?: number | false;
  /** Row limit for OHLCV and indicator queries. Defaults to 50000. */
  limit?: number;
}

export interface UseChartIndicatorsResult {
  /** Raw OHLCV candle data */
  candleData:
    | Array<{
        timestamp: string;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
      }>
    | undefined;

  /** EMA + RSI lines for main chart */
  chartIndicators: IndicatorData[];
  /** Bollinger Band data */
  chartBollData: BollingerBandData | undefined;
  /** Volume histogram data */
  volumeData: Array<{ time: string; value: number; color: string }>;
  /** MACD sub-chart data */
  macdData: {
    macd: Array<{ time: string; value: number }>;
    signal: Array<{ time: string; value: number }>;
    histogram: Array<{ time: string; value: number }>;
  } | null;
  /** KDJ sub-chart data */
  kdjData: {
    k: Array<{ time: string; value: number }>;
    d: Array<{ time: string; value: number }>;
    j: Array<{ time: string; value: number }>;
  } | null;

  /** Loading state */
  isLoadingOhlcv: boolean;
  isLoadingIndicators: boolean;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChartIndicators(
  params: UseChartIndicatorsParams,
): UseChartIndicatorsResult {
  const {
    symbol,
    timeframe,
    dateRange,
    subChartType = "volume",
    enabled = true,
    refetchInterval = false,
    limit = 50000,
  } = params;
  // G17.1：全量订阅改字段级 selector（本 hook 只消费 ema/boll/rsi 三分区）
  const chartSettings = useChartSettings(
    useShallow((s) => ({ emaList: s.emaList, boll: s.boll, rsi: s.rsi })),
  );

  // Query 1: OHLCV candle data
  const { data: ohlcvResponse, isLoading: isLoadingOhlcv } = useQuery({
    ...ohlcvOptions(symbol, timeframe, dateRange, limit),
    enabled,
    refetchInterval,
  });
  const candleData = ohlcvResponse?.data;

  // Build indicator API params from Zustand store
  const indicatorParams = useMemo(
    () => ({
      start: dateRange.start || undefined,
      end: dateRange.end || undefined,
      emaPeriods: chartSettings.emaList
        .filter((e) => e.enabled)
        .map((e) => e.period)
        .join(","),
      bollEnabled: chartSettings.boll.enabled,
      bollPeriod: chartSettings.boll.period,
      bollStd: chartSettings.boll.std,
      rsiEnabled: subChartType === "rsi",
      rsiPeriod: chartSettings.rsi.period,
      macdEnabled: subChartType === "macd",
      kdjEnabled: subChartType === "kdj",
    }),
    [
      dateRange.start,
      dateRange.end,
      chartSettings.emaList,
      chartSettings.boll.enabled,
      chartSettings.boll.period,
      chartSettings.boll.std,
      chartSettings.rsi.period,
      subChartType,
    ],
  );

  // Query 2: Chart indicators (depends on candle data being available)
  const { data: indicatorResponse, isLoading: isLoadingIndicators } = useQuery({
    ...chartIndicatorOptions(
      symbol,
      timeframe,
      subChartType,
      indicatorParams,
      { enabled: enabled && !!candleData && candleData.length > 0 },
    ),
  });

  // Transform: EMA + RSI lines for KlineChart indicators prop
  const chartIndicators = useMemo<IndicatorData[]>(() => {
    if (!indicatorResponse) return [];
    const indicators: IndicatorData[] = [];

    const emaList = chartSettings.emaList.filter((e) => e.enabled);
    for (const ema of emaList) {
      const emaData = indicatorResponse.ema?.[String(ema.period)];
      if (emaData) {
        indicators.push({
          id: `ema_${ema.period}`,
          type: "ema",
          color: ema.color,
          data: emaData,
        });
      }
    }

    if (subChartType === "rsi" && indicatorResponse.rsi) {
      indicators.push({
        id: "rsi",
        type: "rsi",
        color: "#A78BFA",
        data: indicatorResponse.rsi,
      });
    }

    return indicators;
  }, [indicatorResponse, chartSettings.emaList, subChartType]);

  // Transform: Bollinger Band data
  const chartBollData = useMemo<BollingerBandData | undefined>(() => {
    if (!indicatorResponse?.boll || !chartSettings.boll.enabled) return undefined;
    return indicatorResponse.boll;
  }, [indicatorResponse?.boll, chartSettings.boll.enabled]);

  // Transform: Volume histogram data from candle data
  const volumeData = useMemo(() => {
    if (!candleData) return [];
    return candleData.map((d) => {
      const isUp = d.close >= d.open;
      return {
        time: d.timestamp,
        value: d.volume,
        color: isUp ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)",
      };
    });
  }, [candleData]);

  return {
    candleData,
    chartIndicators,
    chartBollData,
    volumeData,
    macdData: indicatorResponse?.macd ?? null,
    kdjData: indicatorResponse?.kdj ?? null,
    isLoadingOhlcv,
    isLoadingIndicators,
  };
}
