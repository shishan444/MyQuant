import { useLayoutEffect, useEffect, useRef, useMemo, useImperativeHandle, forwardRef, useState, useCallback } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  createSeriesMarkers,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  Time,
  UTCTimestamp,
  MouseEventParams,
} from "lightweight-charts";

import { DARK_CHART_THEME, CHART_COLORS } from "./core/chartThemes";
import { toTime } from "./core/time";
import { useChartSync } from "./core/useChartSync";
import { ChartEmbeddedLegend } from "./ChartEmbeddedLegend";
import { ChartToolbar } from "./ChartToolbar";
import { useChartSettings } from "@/stores/chart-settings";
import { useShallow } from "zustand/react/shallow";
import type { BollingerBandData, MTFIndicatorData, LegendGroup, LegendItem } from "@/types/chart";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CandleData {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface IndicatorData {
  id: string;
  type: string;
  color: string;
  data: Array<{ time: string; value: number }>;
}

type SignalAction = "open" | "close" | "add" | "reduce";
type SignalSide = "long" | "short";

interface SignalData {
  type: "buy" | "sell" | `${SignalAction}_${SignalSide}`;
  timestamp: string;
}

interface TriggerMarker {
  id: number;
  time: string;
  matched: boolean;
  subtype?: string; // "double_top" | "head_shoulders_top" | "triple_top"
}

interface KlineChartProps {
  data: CandleData[];
  indicators?: IndicatorData[];
  signals?: SignalData[];
  triggers?: TriggerMarker[];
  height?: number;
  onTriggerClick?: (id: number) => void;
  bollData?: BollingerBandData;
  volumeData?: Array<{ time: string; value: number; color?: string }>;
  mtfIndicators?: MTFIndicatorData[];
  /** Called once after chart and series are created. */
  onChartReady?: (chart: IChartApi, series: ISeriesApi<"Candlestick">) => void;
  subChartType?: "volume" | "macd" | "rsi" | "kdj" | "equity";
  macdData?: { macd: Array<{ time: string; value: number }>; signal: Array<{ time: string; value: number }>; histogram: Array<{ time: string; value: number }> } | null;
  kdjData?: { k: Array<{ time: string; value: number }>; d: Array<{ time: string; value: number }>; j: Array<{ time: string; value: number }> } | null;
  equityData?: Array<{ time: string; value: number }>;
}

export interface KlineChartHandle {
  scrollToTime: (time: string) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetView: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toCandleData(data: CandleData[]) {
  const mapped = data.map((d) => ({
    time: toTime(d.timestamp),
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
  }));
  return mapped
    .filter((d) => typeof d.time === "number" && !isNaN(d.time as number))
    .sort((a, b) => (a.time as number) - (b.time as number));
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const KlineChart = forwardRef<KlineChartHandle, KlineChartProps>(function KlineChart({
  data,
  indicators,
  signals,
  triggers,
  height = 450,
  onTriggerClick: _onTriggerClick,
  bollData,
  volumeData,
  mtfIndicators,
  onChartReady,
  subChartType = "rsi",
  macdData,
  kdjData,
  equityData,
}, ref) {
  const mainRef = useRef<HTMLDivElement>(null);
  const subChartDivRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const mainChartRef = useRef<IChartApi | null>(null);
  const subChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const indicatorSeriesRefs = useRef<Map<string, ISeriesApi<"Line">>>(
    new Map(),
  );
  const subChartSeriesRefs = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const bollSeriesRefs = useRef<ISeriesApi<"Line">[]>([]);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const mtfSeriesRefs = useRef<ISeriesApi<"Line">[]>([]);
  const triggerOffsetSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const initialFitDoneRef = useRef(false);
  const onChartReadyRef = useRef(onChartReady);
  onChartReadyRef.current = onChartReady;

  // Legend state
  const [legendGroups, setLegendGroups] = useState<LegendGroup[]>([]);
  const [legendValues, setLegendValues] = useState<Record<string, string>>({});

  // Chart settings from store（G17.1：全量订阅改字段级 selector）
  const chartSettings = useChartSettings(
    useShallow((s) => ({ emaList: s.emaList, boll: s.boll })),
  );

  // Fullscreen state
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Stable callbacks so effect dependencies stay clean.
  const memoizedCandles = useMemo(() => toCandleData(data), [data]);
  const memoizedIndicators = useMemo(() => indicators ?? [], [indicators]);
  const memoizedSignals = useMemo(() => signals ?? [], [signals]);
  const memoizedTriggers = useMemo(() => triggers ?? [], [triggers]);
  const memoizedBollData = useMemo(() => bollData, [bollData]);
  const memoizedVolumeData = useMemo(() => volumeData, [volumeData]);
  const memoizedMtfIndicators = useMemo(() => mtfIndicators ?? [], [mtfIndicators]);

  // Expose imperative handle
  useImperativeHandle(ref, () => ({
    scrollToTime: (time: string) => {
      const chart = mainChartRef.current;
      if (!chart) return;
      const ts = toTime(time);
      chart.timeScale().setVisibleRange({
        from: ts as UTCTimestamp,
        to: ts as UTCTimestamp,
      });
    },
    zoomIn: () => {
      const chart = mainChartRef.current;
      if (!chart) return;
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) {
        const barCount = range.to - range.from;
        const center = (range.from + range.to) / 2;
        const newBarCount = barCount * 0.8;
        chart.timeScale().setVisibleLogicalRange({
          from: Math.round(center - newBarCount / 2),
          to: Math.round(center + newBarCount / 2),
        });
      }
    },
    zoomOut: () => {
      const chart = mainChartRef.current;
      if (!chart) return;
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) {
        const barCount = range.to - range.from;
        const center = (range.from + range.to) / 2;
        const newBarCount = barCount * 1.25;
        chart.timeScale().setVisibleLogicalRange({
          from: Math.round(center - newBarCount / 2),
          to: Math.round(center + newBarCount / 2),
        });
      }
    },
    resetView: () => {
      mainChartRef.current?.timeScale().fitContent();
    },
  }), []);

  // Sync charts via shared hook.
  useChartSync(mainChartRef.current, subChartRef.current);

  // Build legend groups from current data
  useEffect(() => {
    const groups: LegendGroup[] = [];

    // OHLCV group
    groups.push({
      id: "ohlcv",
      label: "OHLCV",
      items: [
        { id: "candle", label: "K线", color: "#94a3b8", visible: true },
      ],
    });

    // EMA group from store
    const emaItems: LegendItem[] = chartSettings.emaList
      .filter((e) => e.enabled)
      .map((e) => ({
        id: `ema_${e.period}`,
        label: `EMA(${e.period})`,
        color: e.color,
        visible: true,
      }));
    if (emaItems.length > 0) {
      groups.push({ id: "ema", label: "趋势", items: emaItems });
    }

    // BOLL group
    if (memoizedBollData && chartSettings.boll.enabled) {
      groups.push({
        id: "boll",
        label: "BOLL",
        items: [
          { id: "boll_upper", label: "上轨", color: "#F59E0B", visible: true },
          { id: "boll_middle", label: "中轨", color: "#F59E0B88", visible: true },
          { id: "boll_lower", label: "下轨", color: "#F59E0B", visible: true },
        ],
      });
    }

    // MTF group
    if (memoizedMtfIndicators.length > 0) {
      groups.push({
        id: "mtf",
        label: "跨周期",
        items: memoizedMtfIndicators.map((m) => ({
          id: `mtf_${m.sourceTimeframe}_${m.indicatorName}`,
          label: `${m.sourceTimeframe.toUpperCase()} ${m.indicatorName}`,
          color: m.color,
          visible: true,
        })),
      });
    }

    // Triggers group
    if (memoizedTriggers.length > 0) {
      groups.push({
        id: "triggers",
        label: "触发点",
        items: [
          { id: "trigger_markers", label: `触发 (${memoizedTriggers.length})`, color: "#00C853", visible: true },
        ],
      });
    }

    setLegendGroups(groups);
  }, [chartSettings.emaList, chartSettings.boll.enabled, memoizedBollData, memoizedMtfIndicators, memoizedTriggers]);

  const handleLegendToggle = useCallback((groupId: string, itemId: string) => {
    // Toggle visibility by applying transparent color to hidden series
    const seriesMap = indicatorSeriesRefs.current;
    const bollSeries = bollSeriesRefs.current;
    const mtfSeries = mtfSeriesRefs.current;

    // Try to find the series in various ref maps
    const targetSeries =
      seriesMap.get(itemId) ??
      (groupId === "boll" ? bollSeries[["boll_upper", "boll_middle", "boll_lower"].indexOf(itemId)] : null) ??
      (groupId === "mtf" ? mtfSeries.find((s) => s.options().title === itemId) : null);

    if (targetSeries) {
      const currentColor = targetSeries.options().color as string;
      if (currentColor === "transparent") {
        // Restore: we need to know the original color, simplified approach
        targetSeries.applyOptions({ color: "#F59E0B" });
      } else {
        targetSeries.applyOptions({ color: "transparent" });
      }
    }
  }, []);

  // -------------------------------------------------------------------------
  // Chart creation / destruction
  // -------------------------------------------------------------------------
  useLayoutEffect(() => {
    if (!mainRef.current || !subChartDivRef.current) return;

    const mainHeight = Math.round(height * 0.78);
    const subHeight = Math.round(height * 0.22);

    // -- Main chart --
    const mainChart = createChart(mainRef.current, {
      ...DARK_CHART_THEME,
      width: mainRef.current.clientWidth,
      height: mainHeight,
      rightPriceScale: {
        ...DARK_CHART_THEME.rightPriceScale,
        autoScale: true,
        scaleMargins: {
          top: 0.15,
          bottom: 0.10,
        },
      },
    });

    const candleSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: CHART_COLORS.candleUp,
      downColor: CHART_COLORS.candleDown,
      borderVisible: false,
      wickUpColor: CHART_COLORS.candleUp,
      wickDownColor: CHART_COLORS.candleDown,
    });

    mainChartRef.current = mainChart;
    candleSeriesRef.current = candleSeries;

    // Notify parent that chart is ready
    if (onChartReadyRef.current) {
      onChartReadyRef.current(mainChart, candleSeries);
    }

    // -- Sub-chart (generic, series created dynamically per type) --
    const subChart = createChart(subChartDivRef.current, {
      ...DARK_CHART_THEME,
      width: subChartDivRef.current.clientWidth,
      height: subHeight,
      timeScale: {
        ...DARK_CHART_THEME.timeScale,
        visible: false,
      },
    });

    subChartRef.current = subChart;

    // -- ResizeObserver --
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        if (entry.target === mainRef.current) {
          mainChart.applyOptions({ width });
        } else if (entry.target === subChartDivRef.current) {
          subChart.applyOptions({ width });
        }
      }
    });

    resizeObserver.observe(mainRef.current);
    resizeObserver.observe(subChartDivRef.current);

    // Crosshair move for legend values
    mainChart.subscribeCrosshairMove((param: MouseEventParams) => {
      if (!param.time || !param.seriesData) {
        return;
      }
      const vals: Record<string, string> = {};
      const candleData = param.seriesData.get(candleSeries);
      if (candleData && "close" in candleData) {
        vals["candle"] = `C: ${(candleData as { close: number }).close.toFixed(2)}`;
      }
      // Update indicator values
      for (const [id, series] of indicatorSeriesRefs.current) {
        const sd = param.seriesData.get(series);
        if (sd && "value" in sd) {
          vals[id] = (sd as { value: number }).value.toFixed(2);
        }
      }
      setLegendValues(vals);
    });

    // autoScale + scaleMargins ensures signal markers are always visible
    // fitContent() handles price range including all markers

    return () => {
      resizeObserver.disconnect();
      mainChart.remove();
      subChart.remove();
      mainChartRef.current = null;
      subChartRef.current = null;
      candleSeriesRef.current = null;
      indicatorSeriesRefs.current.clear();
      markersPluginRef.current = null;
      subChartSeriesRefs.current = [];
      bollSeriesRefs.current = [];
      volumeSeriesRef.current = null;
      mtfSeriesRefs.current = [];
      triggerOffsetSeriesRef.current = null;
      initialFitDoneRef.current = false;
    };
  }, [height]);

  // -------------------------------------------------------------------------
  // Data updates
  // -------------------------------------------------------------------------
  useEffect(() => {
    const mainChart = mainChartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!mainChart || !candleSeries) return;

    // -- Candle data --
    candleSeries.setData(memoizedCandles);

    // -- Remove stale indicator series --
    for (const [id, series] of indicatorSeriesRefs.current) {
      const stillExists = memoizedIndicators.some((i) => i.id === id);
      if (!stillExists) {
        mainChart.removeSeries(series);
        indicatorSeriesRefs.current.delete(id);
      }
    }

    // -- Indicator lines --
    for (const indicator of memoizedIndicators) {
      let series = indicatorSeriesRefs.current.get(indicator.id);
      if (!series) {
        series = mainChart.addSeries(LineSeries, {
          color: indicator.color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
        });
        indicatorSeriesRefs.current.set(indicator.id, series);
      }
      series.setData(
        indicator.data.map((d) => ({ time: toTime(d.time), value: d.value })),
      );
    }

    // -- EMA lines from chart settings --
    const emaList = chartSettings.emaList.filter((e) => e.enabled);
    // Remove old EMA series
    for (const [id, series] of indicatorSeriesRefs.current) {
      if (id.startsWith("ema_") && !emaList.some((e) => `ema_${e.period}` === id)) {
        mainChart.removeSeries(series);
        indicatorSeriesRefs.current.delete(id);
      }
    }
    // EMA data comes from the indicators prop (computed by backend), so we don't create new series here
    // The EMA series are handled through the indicators prop

    // -- BOLL bands --
    for (const s of bollSeriesRefs.current) {
      mainChart.removeSeries(s);
    }
    bollSeriesRefs.current = [];

    if (memoizedBollData && chartSettings.boll.enabled) {
      const bollColor = chartSettings.boll.color || "#F59E0B";

      const upperSeries = mainChart.addSeries(LineSeries, {
        color: bollColor,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "boll_upper",
      });
      upperSeries.setData(memoizedBollData.upper.map((d) => ({ time: toTime(d.time), value: d.value })));

      const middleSeries = mainChart.addSeries(LineSeries, {
        color: `${bollColor}88`,
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "boll_middle",
      });
      middleSeries.setData(memoizedBollData.middle.map((d) => ({ time: toTime(d.time), value: d.value })));

      const lowerSeries = mainChart.addSeries(LineSeries, {
        color: bollColor,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "boll_lower",
      });
      lowerSeries.setData(memoizedBollData.lower.map((d) => ({ time: toTime(d.time), value: d.value })));

      bollSeriesRefs.current = [upperSeries, middleSeries, lowerSeries];
    }

    // -- Volume --
    if (volumeSeriesRef.current) {
      mainChart.removeSeries(volumeSeriesRef.current);
      volumeSeriesRef.current = null;
    }

    if (memoizedVolumeData && memoizedVolumeData.length > 0) {
      const volSeries = mainChart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
      });
      mainChart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volSeries.setData(
        memoizedVolumeData.map((d) => ({
          time: toTime(d.time),
          value: d.value,
          color: d.color ?? "rgba(100,116,139,0.3)",
        })),
      );
      volumeSeriesRef.current = volSeries;
    }

    // -- MTF indicators --
    for (const s of mtfSeriesRefs.current) {
      mainChart.removeSeries(s);
    }
    mtfSeriesRefs.current = [];

    if (memoizedMtfIndicators.length > 0) {
      for (const mtf of memoizedMtfIndicators) {
        const mtfSeries = mainChart.addSeries(LineSeries, {
          color: mtf.color,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          title: `mtf_${mtf.sourceTimeframe}_${mtf.indicatorName}`,
        });
        mtfSeries.setData(
          mtf.data.map((d) => ({ time: toTime(d.time), value: d.value })),
        );
        mtfSeriesRefs.current.push(mtfSeries);
      }
    }

    // -- Build markers (signals on candle, triggers on offset series) --

    // Remove old trigger offset series
    if (triggerOffsetSeriesRef.current) {
      mainChart.removeSeries(triggerOffsetSeriesRef.current);
      triggerOffsetSeriesRef.current = null;
    }

    // Signal markers stay on candle series
    const SIGNAL_STYLE: Record<string, {
      position: "aboveBar" | "belowBar";
      color: string;
      shape: "arrowUp" | "arrowDown" | "circle" | "square";
      text: string;
    }> = {
      // Legacy (Lab backtest signals)
      buy: { position: "belowBar", color: "#00C853", shape: "arrowUp", text: "B" },
      sell: { position: "aboveBar", color: "#FF1744", shape: "arrowDown", text: "S" },
      // Paper trading - Long
      open_long: { position: "belowBar", color: "#00C853", shape: "arrowUp", text: "OL" },
      close_long: { position: "aboveBar", color: "#FF9800", shape: "arrowDown", text: "CL" },
      add_long: { position: "belowBar", color: "#00C853", shape: "circle", text: "AL" },
      reduce_long: { position: "aboveBar", color: "#FF9800", shape: "circle", text: "RL" },
      // Paper trading - Short
      open_short: { position: "aboveBar", color: "#FF1744", shape: "arrowDown", text: "OS" },
      close_short: { position: "belowBar", color: "#7C4DFF", shape: "arrowUp", text: "CS" },
      add_short: { position: "aboveBar", color: "#FF1744", shape: "square", text: "AS" },
      reduce_short: { position: "belowBar", color: "#7C4DFF", shape: "square", text: "RS" },
    };

    const markers: Array<{
      time: Time;
      position: "aboveBar" | "belowBar";
      color: string;
      shape: "arrowUp" | "arrowDown" | "circle" | "square";
      text: string;
    }> = [];

    for (const s of memoizedSignals) {
      const style = SIGNAL_STYLE[s.type] ?? SIGNAL_STYLE.buy;
      markers.push({
        time: toTime(s.timestamp),
        ...style,
      });
    }

    if (markersPluginRef.current) {
      markersPluginRef.current.setMarkers(markers);
    } else {
      markersPluginRef.current = createSeriesMarkers(candleSeries, markers);
    }

    // Trigger markers on offset series (1.2% above candle high)
    if (memoizedTriggers.length > 0) {
      const candleHighMap = new Map<number, number>();
      for (const c of memoizedCandles) {
        candleHighMap.set(c.time as number, c.high);
      }

      const TRIGGER_OFFSET = 1.012;
      const offsetData: Array<{ time: Time; value: number }> = [];
      const triggerMarkers: Array<{
        time: Time;
        position: "aboveBar" | "belowBar";
        color: string;
        shape: "circle";
        text: string;
      }> = [];

      const SUBTYPE_COLORS: Record<string, string> = {
        double_top: "#F59E0B",
        head_shoulders_top: "#A855F7",
        triple_top: "#3B82F6",
      };

      const SUBTYPE_LABELS: Record<string, string> = {
        double_top: "M",
        head_shoulders_top: "H&S",
        triple_top: "T",
      };

      for (const t of memoizedTriggers) {
        const triggerTime = toTime(t.time);
        const high = candleHighMap.get(triggerTime as number);
        if (high !== undefined) {
          offsetData.push({ time: triggerTime, value: high * TRIGGER_OFFSET });
          const subtypeColor = t.subtype ? SUBTYPE_COLORS[t.subtype] : undefined;
          const subtypeLabel = t.subtype ? SUBTYPE_LABELS[t.subtype] : undefined;
          triggerMarkers.push({
            time: triggerTime,
            position: "aboveBar",
            color: subtypeColor ?? (t.matched ? "#00C853" : "#EF4444"),
            shape: "circle",
            text: subtypeLabel ? `${subtypeLabel}(${t.id})` : `(${t.id})`,
          });
        }
      }

      if (offsetData.length > 0) {
        const offsetSeries = mainChart.addSeries(LineSeries, {
          color: "transparent",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        offsetSeries.setData(offsetData);
        triggerOffsetSeriesRef.current = offsetSeries;
        createSeriesMarkers(offsetSeries, triggerMarkers);
      }
    }

    // -- Fit content and set price range --
    if (!initialFitDoneRef.current && memoizedCandles.length > 0) {
      mainChart.timeScale().fitContent();
      initialFitDoneRef.current = true;
    }
  }, [memoizedCandles, memoizedIndicators, memoizedSignals, memoizedTriggers, memoizedBollData, memoizedVolumeData, memoizedMtfIndicators, chartSettings.boll]);

  // -------------------------------------------------------------------------
  // Sub-chart data update (generic: supports volume, macd, rsi, kdj)
  // -------------------------------------------------------------------------
  const rsiIndicator = memoizedIndicators.find((i) => i.type === "rsi");
  const rsiData = useMemo(
    () =>
      rsiIndicator
        ? rsiIndicator.data.map((d) => ({
            time: toTime(d.time),
            value: d.value,
          }))
        : [],
    [rsiIndicator],
  );

  const memoizedMacdData = useMemo(() => macdData, [macdData]);
  const memoizedKdjData = useMemo(() => kdjData, [kdjData]);
  const memoizedEquityData = useMemo(() => equityData ?? [], [equityData]);
  const memoizedSubChartType = useMemo(() => subChartType, [subChartType]);

  useEffect(() => {
    const subChart = subChartRef.current;
    if (!subChart) return;

    // Remove all existing series
    for (const s of subChartSeriesRefs.current) {
      subChart.removeSeries(s);
    }
    subChartSeriesRefs.current = [];

    const createdSeries: ISeriesApi<"Line" | "Histogram">[] = [];

    if (memoizedSubChartType === "volume") {
      // Volume sub-chart: histogram with green/red bars
      const volSrc = memoizedVolumeData ?? [];
      if (volSrc.length > 0) {
        const histSeries = subChart.addSeries(HistogramSeries, {
          priceFormat: { type: "volume" },
          priceLineVisible: false,
          lastValueVisible: true,
        });
        histSeries.setData(
          volSrc.map((d) => ({
            time: toTime(d.time),
            value: d.value,
            color: d.color ?? "rgba(100,116,139,0.3)",
          })),
        );
        createdSeries.push(histSeries);
      }
    } else if (memoizedSubChartType === "macd" && memoizedMacdData) {
      // MACD sub-chart: histogram + macd line + signal line
      const histogramSeries = subChart.addSeries(HistogramSeries, {
        priceLineVisible: false,
        lastValueVisible: false,
      });
      histogramSeries.setData(
        memoizedMacdData.histogram.map((d) => ({
          time: toTime(d.time),
          value: d.value,
          color: d.value >= 0 ? "rgba(34,197,94,0.6)" : "rgba(239,68,68,0.6)",
        })),
      );
      createdSeries.push(histogramSeries);

      const macdLine = subChart.addSeries(LineSeries, {
        color: "#3B82F6",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      macdLine.setData(memoizedMacdData.macd.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(macdLine);

      const signalLine = subChart.addSeries(LineSeries, {
        color: "#F59E0B",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      signalLine.setData(memoizedMacdData.signal.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(signalLine);
    } else if (memoizedSubChartType === "rsi") {
      // RSI sub-chart: line + 70/30 reference lines
      if (rsiData.length > 0) {
        const rsiSeries = subChart.addSeries(LineSeries, {
          color: CHART_COLORS.rsi,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
        });
        rsiSeries.setData(rsiData);
        createdSeries.push(rsiSeries);

        const time = rsiData[0].time;
        const lastTime = rsiData[rsiData.length - 1].time;

        const obSeries = subChart.addSeries(LineSeries, {
          color: CHART_COLORS.overbought,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        obSeries.setData([{ time, value: 70 }, { time: lastTime, value: 70 }]);
        createdSeries.push(obSeries);

        const osSeries = subChart.addSeries(LineSeries, {
          color: CHART_COLORS.oversold,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        osSeries.setData([{ time, value: 30 }, { time: lastTime, value: 30 }]);
        createdSeries.push(osSeries);
      }
    } else if (memoizedSubChartType === "kdj" && memoizedKdjData) {
      // KDJ sub-chart: K/D/J lines + 80/20 reference lines
      const kLine = subChart.addSeries(LineSeries, {
        color: "#3B82F6",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      kLine.setData(memoizedKdjData.k.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(kLine);

      const dLine = subChart.addSeries(LineSeries, {
        color: "#F59E0B",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      dLine.setData(memoizedKdjData.d.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(dLine);

      const jLine = subChart.addSeries(LineSeries, {
        color: "#A78BFA",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      jLine.setData(memoizedKdjData.j.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(jLine);

      // 80/20 reference lines
      const allKdjPoints = memoizedKdjData.k;
      if (allKdjPoints.length > 0) {
        const time = toTime(allKdjPoints[0].time);
        const lastTime = toTime(allKdjPoints[allKdjPoints.length - 1].time);

        const ref80 = subChart.addSeries(LineSeries, {
          color: "rgba(239,68,68,0.4)",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        ref80.setData([{ time, value: 80 }, { time: lastTime, value: 80 }]);
        createdSeries.push(ref80);

        const ref20 = subChart.addSeries(LineSeries, {
          color: "rgba(34,197,94,0.4)",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        ref20.setData([{ time, value: 20 }, { time: lastTime, value: 20 }]);
        createdSeries.push(ref20);
      }
    } else if (memoizedSubChartType === "equity" && memoizedEquityData.length > 0) {
      // Equity curve sub-chart: line series showing portfolio value over time
      const equitySeries = subChart.addSeries(LineSeries, {
        color: "#f59e0b",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Equity",
      });
      equitySeries.setData(
        memoizedEquityData.map((d) => ({ time: toTime(d.time), value: d.value })),
      );
      createdSeries.push(equitySeries);

      // Reference line at initial capital (first value)
      const firstVal = memoizedEquityData[0].value;
      const eqStartTime = toTime(memoizedEquityData[0].time);
      const eqEndTime = toTime(memoizedEquityData[memoizedEquityData.length - 1].time);
      const refLine = subChart.addSeries(LineSeries, {
        color: "rgba(148,163,184,0.3)",
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      refLine.setData([
        { time: eqStartTime, value: firstVal },
        { time: eqEndTime, value: firstVal },
      ]);
      createdSeries.push(refLine);
    }

    subChartSeriesRefs.current = createdSeries;
    subChart.timeScale().fitContent();
  }, [memoizedSubChartType, rsiData, memoizedMacdData, memoizedKdjData, memoizedVolumeData, memoizedEquityData]);

  // Fullscreen toggle
  const handleFullscreen = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  }, []);

  return (
    <div
      ref={containerRef}
      className="relative"
      style={{ height: isFullscreen ? "100vh" : height }}
    >
      {/* Toolbar */}
      <div className="absolute right-2 top-2 z-20">
        <ChartToolbar
          onZoomIn={() => {
            const chart = mainChartRef.current;
            if (!chart) return;
            const range = chart.timeScale().getVisibleLogicalRange();
            if (range) {
              const barCount = range.to - range.from;
              const center = (range.from + range.to) / 2;
              const newBarCount = barCount * 0.8;
              chart.timeScale().setVisibleLogicalRange({
                from: Math.round(center - newBarCount / 2),
                to: Math.round(center + newBarCount / 2),
              });
            }
          }}
          onZoomOut={() => {
            const chart = mainChartRef.current;
            if (!chart) return;
            const range = chart.timeScale().getVisibleLogicalRange();
            if (range) {
              const barCount = range.to - range.from;
              const center = (range.from + range.to) / 2;
              const newBarCount = barCount * 1.25;
              chart.timeScale().setVisibleLogicalRange({
                from: Math.round(center - newBarCount / 2),
                to: Math.round(center + newBarCount / 2),
              });
            }
          }}
          onReset={() => {
            const chart = mainChartRef.current;
            if (!chart) return;
            chart.timeScale().fitContent();
          }}
          onFullscreen={handleFullscreen}
        />
      </div>

      {/* Legend */}
      <ChartEmbeddedLegend
        groups={legendGroups}
        onToggle={handleLegendToggle}
        values={legendValues}
      />

      <div className="flex flex-col h-full">
        <div ref={mainRef} className="flex-[3] min-h-0" />
        <div
          ref={subChartDivRef}
          className="flex-1 min-h-0 border-t border-slate-800/50"
        />
      </div>
    </div>
  );
});

export { KlineChart };
export type { KlineChartProps, CandleData, IndicatorData, SignalData, TriggerMarker };
