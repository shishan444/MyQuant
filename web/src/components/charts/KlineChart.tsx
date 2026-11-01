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
import { signalsToMarkers } from "./primitives";
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
  /** 图表与 series 创建完成后回调一次。 */
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
  const containerRef = useRef<HTMLDivElement>(null);

  const mainChartRef = useRef<IChartApi | null>(null);
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

  // 图表设置来自 settings store（G17.1：全量订阅改字段级 selector）
  const chartSettings = useChartSettings(
    useShallow((s) => ({ emaList: s.emaList, boll: s.boll })),
  );

  // Fullscreen state
  const [isFullscreen, setIsFullscreen] = useState(false);

  // memo 化保证引用稳定，effect 依赖数组不随渲染抖动
  const memoizedCandles = useMemo(() => toCandleData(data), [data]);
  const memoizedIndicators = useMemo(() => indicators ?? [], [indicators]);
  const memoizedSignals = useMemo(() => signals ?? [], [signals]);
  const memoizedTriggers = useMemo(() => triggers ?? [], [triggers]);
  const memoizedBollData = useMemo(() => bollData, [bollData]);
  const memoizedVolumeData = useMemo(() => volumeData, [volumeData]);
  const memoizedMtfIndicators = useMemo(() => mtfIndicators ?? [], [mtfIndicators]);

  // 暴露命令式句柄（scrollToTime/zoomIn/zoomOut/resetView）
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

  // 从当前数据构建图例分组
  useEffect(() => {
    const groups: LegendGroup[] = [];

    // OHLCV 组
    groups.push({
      id: "ohlcv",
      label: "OHLCV",
      items: [
        { id: "candle", label: "K线", color: "#94a3b8", visible: true },
      ],
    });

    // EMA 组（来自 settings store）
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

    // BOLL 组
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

    // MTF 组
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

    // 触发点组
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
    // 切换可见性=对隐藏 series 施加透明色（lightweight-charts 无原生 visible 开关）
    const seriesMap = indicatorSeriesRefs.current;
    const bollSeries = bollSeriesRefs.current;
    const mtfSeries = mtfSeriesRefs.current;

    // 依次在各 ref 映射中定位目标 series
    const targetSeries =
      seriesMap.get(itemId) ??
      (groupId === "boll" ? bollSeries[["boll_upper", "boll_middle", "boll_lower"].indexOf(itemId)] : null) ??
      (groupId === "mtf" ? mtfSeries.find((s) => s.options().title === itemId) : null);

    if (targetSeries) {
      const currentColor = targetSeries.options().color as string;
      if (currentColor === "transparent") {
        // 恢复色：原色未单独存档，简化以 BOLL 黄兜底（已知局限）
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
    if (!mainRef.current) return;

    // -- Main chart --
    const mainChart = createChart(mainRef.current, {
      ...DARK_CHART_THEME,
      width: mainRef.current.clientWidth,
      height,
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

    // 通知父组件图表就绪
    if (onChartReadyRef.current) {
      onChartReadyRef.current(mainChart, candleSeries);
    }

    // -- Sub-chart (generic, series created dynamically per type) --
    // kline-common-layer 组 3：单 chart 多 pane（共用时间轴，替代双 chart+useChartSync）
    const subPane = mainChart.addPane();
    subPane.setStretchFactor(0.22);
    mainChart.panes()[0].setStretchFactor(0.78);

    // -- ResizeObserver --
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        if (entry.target === mainRef.current) {
          mainChart.applyOptions({ width });
        }
      }
    });

    resizeObserver.observe(mainRef.current);

    // 十字光标移动时刷新图例数值
    mainChart.subscribeCrosshairMove((param: MouseEventParams) => {
      if (!param.time || !param.seriesData) {
        return;
      }
      const vals: Record<string, string> = {};
      const candleData = param.seriesData.get(candleSeries);
      if (candleData && "close" in candleData) {
        vals["candle"] = `C: ${(candleData as { close: number }).close.toFixed(2)}`;
      }
      // 刷新指标数值
      for (const [id, series] of indicatorSeriesRefs.current) {
        const sd = param.seriesData.get(series);
        if (sd && "value" in sd) {
          vals[id] = (sd as { value: number }).value.toFixed(2);
        }
      }
      setLegendValues(vals);
    });

    // autoScale+scaleMargins 保证信号标记不出可视域；fitContent() 价格区间覆盖全部标记

    return () => {
      resizeObserver.disconnect();
      mainChart.remove();
      mainChartRef.current = null;
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
    // 移除失效 EMA series
    for (const [id, series] of indicatorSeriesRefs.current) {
      if (id.startsWith("ema_") && !emaList.some((e) => `ema_${e.period}` === id)) {
        mainChart.removeSeries(series);
        indicatorSeriesRefs.current.delete(id);
      }
    }
    // EMA 数据经 indicators prop 到达（后端计算），此处不新建 series——EMA series 统一走 indicators 通道

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

    // 移除旧触发点偏移 series
    if (triggerOffsetSeriesRef.current) {
      mainChart.removeSeries(triggerOffsetSeriesRef.current);
      triggerOffsetSeriesRef.current = null;
    }

    // 信号标记挂蜡烛 series（样式表与映射收敛在 primitives/markers——CARD-04 双写归一）
    const markers = signalsToMarkers(memoizedSignals, toTime);

    if (markersPluginRef.current) {
      markersPluginRef.current.setMarkers(markers);
    } else {
      markersPluginRef.current = createSeriesMarkers(candleSeries, markers);
    }

    // 触发点标记挂偏移 series（K 线 high 上浮 1.2%——TRIGGER_OFFSET=1.012）
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
    const chart = mainChartRef.current;
    if (!chart) return;

    // 移除全部既有 series（副图 pane=1——kline-common-layer 组 3 panes 化）
    for (const s of subChartSeriesRefs.current) {
      chart.removeSeries(s);
    }
    subChartSeriesRefs.current = [];

    const createdSeries: ISeriesApi<"Line" | "Histogram">[] = [];
    const SUB_PANE = 1;

    if (memoizedSubChartType === "volume") {
      // 成交量副图：红绿柱 histogram
      const volSrc = memoizedVolumeData ?? [];
      if (volSrc.length > 0) {
        const histSeries = chart.addSeries(HistogramSeries, {
          priceFormat: { type: "volume" },
          priceLineVisible: false,
          lastValueVisible: true,
        }, SUB_PANE);
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
      // MACD 副图：histogram+macd 线+signal 线
      const histogramSeries = chart.addSeries(HistogramSeries, {
        priceLineVisible: false,
        lastValueVisible: false,
      }, SUB_PANE);
      histogramSeries.setData(
        memoizedMacdData.histogram.map((d) => ({
          time: toTime(d.time),
          value: d.value,
          color: d.value >= 0 ? "rgba(34,197,94,0.6)" : "rgba(239,68,68,0.6)",
        })),
      );
      createdSeries.push(histogramSeries);

      const macdLine = chart.addSeries(LineSeries, {
        color: "#3B82F6",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      }, SUB_PANE);
      macdLine.setData(memoizedMacdData.macd.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(macdLine);

      const signalLine = chart.addSeries(LineSeries, {
        color: "#F59E0B",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      }, SUB_PANE);
      signalLine.setData(memoizedMacdData.signal.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(signalLine);
    } else if (memoizedSubChartType === "rsi") {
      // RSI 副图：RSI 线+70/30 参考线
      if (rsiData.length > 0) {
        const rsiSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.rsi,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
        }, SUB_PANE);
        rsiSeries.setData(rsiData);
        createdSeries.push(rsiSeries);

        const time = rsiData[0].time;
        const lastTime = rsiData[rsiData.length - 1].time;

        const obSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.overbought,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }, SUB_PANE);
        obSeries.setData([{ time, value: 70 }, { time: lastTime, value: 70 }]);
        createdSeries.push(obSeries);

        const osSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.oversold,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }, SUB_PANE);
        osSeries.setData([{ time, value: 30 }, { time: lastTime, value: 30 }]);
        createdSeries.push(osSeries);
      }
    } else if (memoizedSubChartType === "kdj" && memoizedKdjData) {
      // KDJ 副图：K/D/J 线+80/20 参考线
      const kLine = chart.addSeries(LineSeries, {
        color: "#3B82F6",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      }, SUB_PANE);
      kLine.setData(memoizedKdjData.k.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(kLine);

      const dLine = chart.addSeries(LineSeries, {
        color: "#F59E0B",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      }, SUB_PANE);
      dLine.setData(memoizedKdjData.d.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(dLine);

      const jLine = chart.addSeries(LineSeries, {
        color: "#A78BFA",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
      }, SUB_PANE);
      jLine.setData(memoizedKdjData.j.map((d) => ({ time: toTime(d.time), value: d.value })));
      createdSeries.push(jLine);

      // 80/20 参考线
      const allKdjPoints = memoizedKdjData.k;
      if (allKdjPoints.length > 0) {
        const time = toTime(allKdjPoints[0].time);
        const lastTime = toTime(allKdjPoints[allKdjPoints.length - 1].time);

        const ref80 = chart.addSeries(LineSeries, {
          color: "rgba(239,68,68,0.4)",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }, SUB_PANE);
        ref80.setData([{ time, value: 80 }, { time: lastTime, value: 80 }]);
        createdSeries.push(ref80);

        const ref20 = chart.addSeries(LineSeries, {
          color: "rgba(34,197,94,0.4)",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }, SUB_PANE);
        ref20.setData([{ time, value: 20 }, { time: lastTime, value: 20 }]);
        createdSeries.push(ref20);
      }
    } else if (memoizedSubChartType === "equity" && memoizedEquityData.length > 0) {
      // 权益曲线副图：组合价值随时间变化的 line series
      const equitySeries = chart.addSeries(LineSeries, {
        color: "#f59e0b",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Equity",
      }, SUB_PANE);
      equitySeries.setData(
        memoizedEquityData.map((d) => ({ time: toTime(d.time), value: d.value })),
      );
      createdSeries.push(equitySeries);

      // 初始资金参考线（取首点值）
      const firstVal = memoizedEquityData[0].value;
      const eqStartTime = toTime(memoizedEquityData[0].time);
      const eqEndTime = toTime(memoizedEquityData[memoizedEquityData.length - 1].time);
      const refLine = chart.addSeries(LineSeries, {
        color: "rgba(148,163,184,0.3)",
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      }, SUB_PANE);
      refLine.setData([
        { time: eqStartTime, value: firstVal },
        { time: eqEndTime, value: firstVal },
      ]);
      createdSeries.push(refLine);
    }

    subChartSeriesRefs.current = createdSeries;
    chart.timeScale().fitContent();
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

      <div ref={mainRef} className="h-full" />
    </div>
  );
});

export { KlineChart };
export type { KlineChartProps, CandleData, IndicatorData, SignalData, TriggerMarker };
