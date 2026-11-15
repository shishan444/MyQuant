/**
 * KlineChart——全库唯一的 lightweight-charts K 线渲染组件
 *
 * 职能：统一承担 K 线、指标线、标记与多 pane 副图的可视化渲染，并对消费方
 * 暴露命令式句柄 KlineChartHandle（视口操作与观测序列挂线）。
 * 价值与服务对象：六消费方三条链共用一套渲染形制——策略研究链（Lab
 * hypothesis 模式主图、BacktestModePanel 回测结果图、SceneModePanel 场景
 * 模式图）、实盘呈现链（TradingChart 实盘 K 线与 trail 线、LiveTrading
 * 直播页精简形态）、验证复核链（RecordDetail 验证记录 D2 复核图）。
 * 职责边界：只管渲染与视口命令——数据全经 props 进入（本组件不取数、
 * 不做业务判定）；主题色单源于 chartThemes 与 theme/tokens，指标开关
 * 状态经 stores/chart-settings，标记与观测序列原语经 charts/primitives。
 */
import { POLARITY } from "@/theme/tokens";
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
import { attachObservationSeries, type ObservationPoint } from "./primitives/observationSeries";
import { ChartEmbeddedLegend } from "./ChartEmbeddedLegend";
import { ChartToolbar } from "./ChartToolbar";
import { useChartSettings } from "@/stores/chart-settings";
import { useShallow } from "zustand/react/shallow";
import type { BollingerBandData, MTFIndicatorData, LegendGroup, LegendItem } from "@/types/chart";

// ---------------------------------------------------------------------------
// 类型定义
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
  subtype?: string; // subtype 值域（形态识别子类型）："double_top" | "head_shoulders_top" | "triple_top"
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
  /** 自定义标记通道（upgrade-validation-records-list 5.2 D2）：消费方
   *  自控色/形/文案（色点单源映射）时优先于 signals——两者并存本通道
   *  生效，向后兼容（缺省 undefined 行为与现状逐位一致） */
  signalMarkers?: Array<{
    time: string;
    position: "aboveBar" | "belowBar";
    color: string;
    shape: "arrowUp" | "arrowDown" | "circle" | "square";
    text: string;
    /** 锚定价位（fe-vr-marker-fidelity 契约 5）：非 null=透明锚定通道
     *  （marker 挂该价位的透明 series 数据点上）；null/缺省=蜡烛主
     *  series 通道（现状行为逐位一致——不传 price 的消费方/mock 零影响） */
    price?: number | null;
    /** 动作族（P1 贪心分桶排序键 open<add<reduce<exit；缺省 undefined
     *  排序退化输入序——向后兼容，组装层自 TraceMarkerDraft 透传） */
    action?: "open" | "add" | "reduce" | "exit";
  }>;
  /** 图表与 series 创建完成后回调一次。 */
  onChartReady?: (chart: IChartApi, series: ISeriesApi<"Candlestick">) => void;
  /** 十字光标移动回调（缺省 undefined 零行为变化——signalMarkers 先例
   *  模式；消费方读 param.time/seriesData 呈现悬停读数，无悬停时 param
   *  无 time 由消费方决定空态/尾 bar） */
  onCrosshairMove?: (param: MouseEventParams) => void;
  subChartType?: "volume" | "macd" | "rsi" | "kdj" | "equity";
  macdData?: { macd: Array<{ time: string; value: number }>; signal: Array<{ time: string; value: number }>; histogram: Array<{ time: string; value: number }> } | null;
  kdjData?: { k: Array<{ time: string; value: number }>; d: Array<{ time: string; value: number }>; j: Array<{ time: string; value: number }> } | null;
  equityData?: Array<{ time: string; value?: number }>;
  /** 隐藏内嵌图例（07 页 HUD 浮层承载信息面时传 true——图例分组折叠
   *  能力本身保留，仅该消费方不呈现；fe-asset-data-domain 2.1） */
  hideLegend?: boolean;
}

export interface KlineChartHandle {
  scrollToTime: (time: string) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetView: () => void;
  /**
   * 挂/换/清观测序列线（hypothesis-workbench CARD-01：不暴露 chart 实例的
   * 命令式通道——挂/重挂/清完整生命周期由消费方经本句柄驱动）。
   * 传 null/空数组=清线；NaN/null 值点在原语层产 whitespace 项（未定义期断线不画）。
   */
  setObservationSeries: (
    points: Array<{ time: string; value: number | null }> | null,
  ) => void;
}

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

/** 原始蜡烛数组 → lightweight-charts 数据点：过滤非法时间戳，按时间升序排列（库要求时间升序）。 */
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
// 组件
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
  signalMarkers,
  onChartReady,
  onCrosshairMove,
  subChartType = "rsi",
  macdData,
  kdjData,
  equityData,
  hideLegend,
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
  // 锚定透明 series 组句柄（fe-vr-marker-fidelity 契约 5——effect 重跑
  // 先全量 removeSeries 再重建，数组管理；anchored 空时零 series 零残留）
  const anchoredMarkerSeriesRefs = useRef<ISeriesApi<"Line">[]>([]);
  // 观测序列线句柄（CARD-01 命令式通道——挂/换先清旧，消费方驱动）
  const observationSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const initialFitDoneRef = useRef(false);
  // 首建高度只取首渲染值（useRef 初值语义）——运行时变化走增量通道（CARD-01）
  const initialHeightRef = useRef(height);
  const onChartReadyRef = useRef(onChartReady);
  onChartReadyRef.current = onChartReady;
  const onCrosshairMoveRef = useRef(onCrosshairMove);
  onCrosshairMoveRef.current = onCrosshairMove;

  // 图例状态
  const [legendGroups, setLegendGroups] = useState<LegendGroup[]>([]);
  const [legendValues, setLegendValues] = useState<Record<string, string>>({});

  // 图表设置来自 settings store（G17.1：全量订阅改字段级 selector）
  const chartSettings = useChartSettings(
    useShallow((s) => ({ emaList: s.emaList, boll: s.boll })),
  );

  // 全屏状态
  const [isFullscreen, setIsFullscreen] = useState(false);

  // memo 化保证引用稳定，effect 依赖数组不随渲染抖动
  const memoizedCandles = useMemo(() => toCandleData(data), [data]);
  const memoizedIndicators = useMemo(() => indicators ?? [], [indicators]);
  const memoizedSignals = useMemo(() => signals ?? [], [signals]);
  const memoizedTriggers = useMemo(() => triggers ?? [], [triggers]);
  const memoizedSignalMarkers = useMemo(
    () =>
      signalMarkers?.map((m) => ({ ...m, time: toTime(m.time) }))
        .filter((m) => typeof m.time === "number" && !isNaN(m.time as number))
        .sort((a, b) => (a.time as number) - (b.time as number)) ?? null,
    [signalMarkers],
  );
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
    setObservationSeries: (points: ObservationPoint[] | null) => {
      const chart = mainChartRef.current;
      if (!chart) return;
      // 挂/换先清旧（单线语义）；chart 未就绪或空数据=纯清
      if (observationSeriesRef.current) {
        chart.removeSeries(observationSeriesRef.current);
        observationSeriesRef.current = null;
      }
      if (!points || points.length === 0) return;
      observationSeriesRef.current = attachObservationSeries(chart, points);
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
          { id: "trigger_markers", label: `触发 (${memoizedTriggers.length})`, color: POLARITY.up, visible: true },
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
  // 图表创建 / 销毁
  // -------------------------------------------------------------------------
  // 职责契约：挂载期一次性创建 chart 基础设施（主图与蜡烛 series、副 pane、
  // ResizeObserver、十字光标订阅），卸载期整体销毁并清位全部 series ref。
  // 不承担数据装载——各 series 的数据由下方数据更新 effect 增量挂载。
  useLayoutEffect(() => {
    if (!mainRef.current) return;

    // -- 主图 --
    const mainChart = createChart(mainRef.current, {
      ...DARK_CHART_THEME,
      width: mainRef.current.clientWidth,
      height: initialHeightRef.current,
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

    // -- 副图（通用形态——series 按类型在数据更新段动态创建）--
    // kline-common-layer 组 3：单 chart 多 pane（共用时间轴，替代双 chart+useChartSync）
    const subPane = mainChart.addPane();
    subPane.setStretchFactor(0.25);
    mainChart.panes()[0].setStretchFactor(0.75);

    // -- ResizeObserver 宽度增量通道 --
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        if (entry.target === mainRef.current) {
          mainChart.applyOptions({ width });
        }
      }
    });

    resizeObserver.observe(mainRef.current);

    // 十字光标移动时刷新图例数值+透传消费方回调（缺省 undefined 零行为变化）
    mainChart.subscribeCrosshairMove((param: MouseEventParams) => {
      onCrosshairMoveRef.current?.(param);
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
      anchoredMarkerSeriesRefs.current = []; // 同上防悬垂——残留旧 chart series 会让重放的数据 effect 对新 chart 调 removeSeries 抛错
      observationSeriesRef.current = null; // chart.remove() 已整体销毁，ref 清位防悬垂
      initialFitDoneRef.current = false;
    };
    // 图表全生命周期只建/销毁一次（依赖空数组）：若依赖含 height，变化时
    // 会整体 remove 重建，而数据挂载 effect 不随之重跑，重建后的图表将
    // 永远没有数据——运行时高度变化由下方增量 effect 承接（CARD-01）
  }, []);

  // 运行时高度增量通道：height prop 变化不重建图表，仅调整 chart 高度
  // （与上方 ResizeObserver 的 width 增量通道同形制；六消费方仅
  // RecordDetail 传运行时 height，恒常量消费方不触发）
  useEffect(() => {
    mainChartRef.current?.applyOptions({ height });
  }, [height]);

  // -------------------------------------------------------------------------
  // 数据更新
  // -------------------------------------------------------------------------
  // 职责契约：响应数据类 props 变化，增量同步主图各 series——蜡烛、指标线、
  // BOLL、量能、MTF、信号标记与触发点偏移线（先清失效 series 再挂新数据）；
  // 首次蜡烛装载后 fitContent 一次。
  useEffect(() => {
    const mainChart = mainChartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!mainChart || !candleSeries) return;

    // -- 蜡烛数据 --
    candleSeries.setData(memoizedCandles);

    // -- 清理失效指标 series（id 已不在当前 indicators 中的先移除）--
    for (const [id, series] of indicatorSeriesRefs.current) {
      const stillExists = memoizedIndicators.some((i) => i.id === id);
      if (!stillExists) {
        mainChart.removeSeries(series);
        indicatorSeriesRefs.current.delete(id);
      }
    }

    // -- 指标线 --
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

    // -- EMA 线（启用集来自 chart settings）--
    const emaList = chartSettings.emaList.filter((e) => e.enabled);
    // 移除失效 EMA series
    for (const [id, series] of indicatorSeriesRefs.current) {
      if (id.startsWith("ema_") && !emaList.some((e) => `ema_${e.period}` === id)) {
        mainChart.removeSeries(series);
        indicatorSeriesRefs.current.delete(id);
      }
    }
    // EMA 数据经 indicators prop 到达（后端计算），此处不新建 series——EMA series 统一走 indicators 通道

    // -- BOLL 布林带（上/中/下轨三条线，整体先清后挂）--
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

    // -- 成交量（主图叠加，独立 vol 价格刻度贴底）--
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

    // -- MTF 跨周期指标 --
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

    // -- 构建标记（无价信号标记挂蜡烛 series；有价标记挂锚定价位的
    //    透明 series 组；触发点标记挂偏移 series）--

    // 移除旧触发点偏移 series
    if (triggerOffsetSeriesRef.current) {
      mainChart.removeSeries(triggerOffsetSeriesRef.current);
      triggerOffsetSeriesRef.current = null;
    }

    // 移除旧锚定透明 series 组（fe-vr-marker-fidelity 契约 5：effect
    // 每次重跑先全量 removeSeries 再重建——anchored 空时零 series 零残留）
    for (const s of anchoredMarkerSeriesRefs.current) {
      mainChart.removeSeries(s);
    }
    anchoredMarkerSeriesRefs.current = [];

    // 信号标记双通道拆分（契约 5）：price 非 null=锚定通道（挂该价位
    // 透明 series 数据点）；null/缺省=蜡烛主 series 通道（现状行为
    // 逐位一致——不传 price 的消费方/mock 零影响）。自定义标记通道
    // （signalMarkers）优先——消费方自控色的单源映射（样式表与映射
    // 收敛在 primitives/markers——CARD-04 双写归一）
    const allMarkers = memoizedSignalMarkers
      ? memoizedSignalMarkers
      : signalsToMarkers(memoizedSignals, toTime);
    // 联合元素：signalMarkers 条目（memoizedSignalMarkers 已统一转
    // Time 并按 time 升序；可带 price/action）或 signals 通道
    // SignalMarker——price 仅存于前者，in 判定收窄
    const hasAnchorPrice = (
      m: (typeof allMarkers)[number],
    ): m is NonNullable<typeof memoizedSignalMarkers>[number] & { price: number } =>
      "price" in m && m.price != null;
    const candleM = allMarkers.filter((m) => !hasAnchorPrice(m));
    const anchored = allMarkers.filter(hasAnchorPrice);

    if (markersPluginRef.current) {
      markersPluginRef.current.setMarkers(candleM);
    } else {
      markersPluginRef.current = createSeriesMarkers(candleSeries, candleM);
    }

    // 锚定通道（有价行，契约 5）：P1 动作族优先序+贪心分桶——anchored
    // 按 (time 升序, 动作族序 open<add<reduce<exit, 输入序) 稳定排序后
    // 逐个放入「该 time 槽位空闲的首条 series」（每条 series 维护
    // time 槽表——series 数据同 time 唯一性约束），无空闲槽则新建
    // series。series 条数=最小所需；常见场景（同 bar 各族至多一笔）
    // 每族一条（R8 字面）。action 缺省（不传者）按输入序参与贪心。
    // series 形制逐项照抄 triggers 偏移 series 先例；透明 series 数据
    // 点参与价格轴 autoScale（价远超可视 bar 域时价格轴外扩——先例
    // 既有可接受行为，如实披露）
    if (anchored.length > 0) {
      const FAMILY_ORDER: Record<"open" | "add" | "reduce" | "exit", number> = {
        open: 0, add: 1, reduce: 2, exit: 3,
      };
      // time 已由 memoizedSignalMarkers 统一为 UTCTimestamp（number）——
      // 数值比较即时间升序；稳定排序下同 time 同序号保输入序
      const sorted = [...anchored].sort((a, b) => {
        const t = (a.time as number) - (b.time as number);
        if (t !== 0) return t;
        return (a.action ? FAMILY_ORDER[a.action] : 4) - (b.action ? FAMILY_ORDER[b.action] : 4);
      });
      const groups: Array<{
        series: ISeriesApi<"Line">;
        slots: Set<Time>;
        points: Array<{ time: Time; value: number }>;
        markers: typeof sorted;
      }> = [];
      for (const m of sorted) {
        let g = groups.find((gr) => !gr.slots.has(m.time));
        if (!g) {
          const series = mainChart.addSeries(LineSeries, {
            color: "transparent",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          g = { series, slots: new Set(), points: [], markers: [] };
          groups.push(g);
          anchoredMarkerSeriesRefs.current.push(series);
        }
        g.slots.add(m.time);
        g.points.push({ time: m.time, value: m.price });
        g.markers.push(m);
      }
      for (const g of groups) {
        g.series.setData(g.points);
        createSeriesMarkers(g.series, g.markers);
      }
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
            color: subtypeColor ?? (t.matched ? POLARITY.up : POLARITY.down),
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

    // -- 首次 fitContent 自适应视口（价格区间由 autoScale+scaleMargins 承担）--
    if (!initialFitDoneRef.current && memoizedCandles.length > 0) {
      mainChart.timeScale().fitContent();
      initialFitDoneRef.current = true;
    }
  }, [memoizedCandles, memoizedIndicators, memoizedSignals, memoizedSignalMarkers, memoizedTriggers, memoizedBollData, memoizedVolumeData, memoizedMtfIndicators, chartSettings.boll]);

  // -------------------------------------------------------------------------
  // 副图数据更新（通用：支持 volume/macd/rsi/kdj/equity）
  // -------------------------------------------------------------------------
  // 职责契约：按 subChartType 在副 pane（pane=1）重建全部副图 series——
  // 先整体移除旧 series 再按类型挂新（类型切换=全量重建），完毕 fitContent。
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
          color: d.value >= 0 ? "rgba(45,212,191,0.6)" : "rgba(251,113,133,0.6)", /* T2：POLARITY 60% alpha 衍生 */
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
          color: "rgba(251,113,133,0.4)", /* T2：POLARITY.down 衍生 */
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }, SUB_PANE);
        ref80.setData([{ time, value: 80 }, { time: lastTime, value: 80 }]);
        createdSeries.push(ref80);

        const ref20 = chart.addSeries(LineSeries, {
          color: "rgba(45,212,191,0.4)", /* T2：POLARITY.up 衍生 */
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
        // whitespace 断点项直接透传（无 value 键——库原生断线机制）
        memoizedEquityData.map((d) =>
          d.value !== undefined
            ? { time: toTime(d.time), value: d.value }
            : { time: toTime(d.time) },
        ),
      );
      createdSeries.push(equitySeries);

      // 初始资金参考线（取全序列首个带 value 点——段界 whitespace 项
      // 不承载值；全 whitespace 退化序列无参考线）
      const firstPt = memoizedEquityData.find((d) => d.value !== undefined);
      if (firstPt && firstPt.value !== undefined) {
        const firstVal = firstPt.value;
        const eqStartTime = toTime(firstPt.time);
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
    }

    subChartSeriesRefs.current = createdSeries;
    chart.timeScale().fitContent();
  }, [memoizedSubChartType, rsiData, memoizedMacdData, memoizedKdjData, memoizedVolumeData, memoizedEquityData]);

  // 全屏切换（document Fullscreen API，容器级进出）
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
      {/* 工具栏 */}
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

      {/* 图例 */}
      {!hideLegend && (
        <ChartEmbeddedLegend
          groups={legendGroups}
          onToggle={handleLegendToggle}
          values={legendValues}
        />
      )}

      <div ref={mainRef} className="h-full" />
    </div>
  );
});

export { KlineChart };
export type { KlineChartProps, CandleData, IndicatorData, SignalData, TriggerMarker };
