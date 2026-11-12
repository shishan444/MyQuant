import { useQuery } from "@tanstack/react-query";
import { POLARITY, DOMAIN } from "@/theme/tokens";
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
  /** 当前激活的副图指标，缺省 "volume"。 */
  subChartType?: SubChartType;
  /** 是否执行查询，缺省 true。 */
  enabled?: boolean;
  /** OHLCV 查询的重取间隔（毫秒），缺省 false（不重取）。 */
  refetchInterval?: number | false;
  /** OHLCV 与指标查询的行数上限，缺省 50000。 */
  limit?: number;
}

export interface UseChartIndicatorsResult {
  /** 原始 OHLCV K 线数据 */
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

  /** 主图 EMA+RSI 线 */
  chartIndicators: IndicatorData[];
  /** 布林带数据 */
  chartBollData: BollingerBandData | undefined;
  /** 成交量柱数据 */
  volumeData: Array<{ time: string; value: number; color: string }>;
  /** MACD 副图数据 */
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

  // Query 2：图表指标（依赖 K 线数据已就绪）
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
        color: DOMAIN.paper,
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

  // 转换：由 K 线数据生成成交量柱
  const volumeData = useMemo(() => {
    if (!candleData) return [];
    return candleData.map((d) => {
      const isUp = d.close >= d.open;
      return {
        time: d.timestamp,
        value: d.volume,
        color: isUp ? `${POLARITY.up}66` : `${POLARITY.down}66`,
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
