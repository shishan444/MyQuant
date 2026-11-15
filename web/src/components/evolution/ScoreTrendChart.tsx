/**
 * 进化得分趋势图（lightweight-charts 单库形态，fe-chart-single-lib 组 3）。
 *
 * 数据变换在 utils/evolutionChart.ts（不动）；装配按 design D2 修订清单：
 * 四 LineSeries（bestScore 金/bestFitness 青条件在场/cumulativeBest 半透明
 * 青点线恒在/avgScore 紫蚂蚁线）+双 priceLine 二选一（无 fitness→目标线/
 * 有→达标线）+champion 与种群边界 markers（lightweight 无竖线原语，边界
 * 竖线族改 marker 承载）。容器底色=CHART_VA（琥珀终端风，形制沿旧）。
 * jsdom 降级：内建 canvas 可用性检测，jsdom 下跳过初始化保留容器与空态。
 * 悬停浮层（fix-chart-tooltip-restore F2）：subscribeCrosshairMove 订阅+
 * 十字段富卡片（旧 scoreTrendOption renderPointTooltip 逐字段基准），浮层
 * 只消费事件回调与 props 两源数据（R-A），全 JSX 渲染零 HTML 注入面（R-D）。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  CrosshairMode,
} from "lightweight-charts";
import type {
  AutoscaleInfo,
  IChartApi,
  MouseEventParams,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { withAlpha, SCORE_TREND_AVG_LINE_COLOR, SCORE_TREND_TARGET_LINE_COLOR, SCORE_TREND_AXIS_LABEL_COLOR, SCORE_TREND_GRID_LINE_COLOR } from "@/components/charts/core/chartThemes";
import { CHART_VA, POLARITY, ATTENTION } from "@/theme/tokens";
import {
  computeTooltipPosition,
  type TooltipPositionResult,
} from "@/components/charts/core/tooltipPosition";
import type { EvolutionHistoryRecord } from "@/types/api";
import {
  transformChartData,
  type ChartTransformResult,
  type ChartDataPoint,
} from "@/utils/evolutionChart";

// 系列色归宿（design D5）：scoreLine/fitnessLine/champion 直接 import tokens；
// avgLine/targetLine/axisLabel/grid 四字面已迁 chartThemes 常量区（组 4.1 归宿，
// 字面值原样），本组件经 import 引用。

interface ScoreTrendChartProps {
  records: EvolutionHistoryRecord[];
  targetScore: number;
  targetFitness?: number;
}

/** 悬停浮层字色（F2）：系列同色四值直接引用既有 tokens/chartThemes import
 * （与图线同源）；浮层专属字面按 §1.2 旧 tooltip 字面收编 */
const TOOLTIP_TEXT_COLORS = {
  head: "#cbd5e1",
  headSub: "#64748b",
  deltaUp: "#34d399",
  deltaDown: "#f87171b3",
  avgTrades: "#94a3b8",
  diversityHigh: "#34d399",
  diversityMid: "#fbbf24",
  diversityLow: "#f87171",
  stagnation: "#fb923cb3",
  newPopulation: "#22d3eecc",
  champion: "#34d399",
  divider: "rgba(51,65,85,0.3)",
} as const;

/** 悬停态存过滤后 ChartDataPoint 原始引用（格式化在 JSX 内完成，回调薄） */
interface ScoreHoverState {
  point: ChartDataPoint;
  x: number;
  y: number;
}

/** generation → epoch 秒承载（lightweight time 轴数值原语；tick 文本回显代数） */
const genToTime = (gen: number) => gen as UTCTimestamp;

interface TrendSeries {
  bestScore: ISeriesApi<"Line"> | null;
  bestFitness: ISeriesApi<"Line"> | null;
  cumulativeBest: ISeriesApi<"Line"> | null;
  avgScore: ISeriesApi<"Line"> | null;
}

const EMPTY_SERIES: TrendSeries = {
  bestScore: null,
  bestFitness: null,
  cumulativeBest: null,
  avgScore: null,
};

export function ScoreTrendChart({
  records,
  targetScore,
  targetFitness = 1.0,
}: ScoreTrendChartProps) {
  const { data, championChanges, stats }: ChartTransformResult = useMemo(
    () => transformChartData(records),
    [records],
  );

  const hasFitnessData = data.some((d) => d.bestFitness != null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<TrendSeries>(EMPTY_SERIES);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // 悬停浮层三通道：safeDataRef（数据 effect 写入过滤产物、订阅回调读——订阅
  // 挂初始化 effect 只跑一次而数据随 props 重跑，直接闭包捕获会陈旧）、hover
  // state（存原始引用）、tooltipPos（measure 后的绝对定位值）
  const safeDataRef = useRef<ChartDataPoint[]>([]);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<ScoreHoverState | null>(null);
  const [tooltipPos, setTooltipPos] = useState<TooltipPositionResult | null>(null);
  const hasData = data.length > 0;
  // jsdom 降级：getContext("2d") 调用判空（jsdom 方法存在但返回 null，无绘制面）
  const canDraw = typeof document !== "undefined" &&
    document.createElement("canvas").getContext("2d") != null;

  useEffect(() => {
    if (!hasData || !canDraw || !containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: SCORE_TREND_AXIS_LABEL_COLOR },
      grid: {
        vertLines: { color: SCORE_TREND_GRID_LINE_COLOR, style: LineStyle.Dotted },
        horzLines: { color: SCORE_TREND_GRID_LINE_COLOR, style: LineStyle.Dotted },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: false,
        secondsVisible: false,
        // x 轴=代数（旧 category 轴形制随迁：epoch 秒承载，tick 文本回代数）
        tickMarkFormatter: (time: Time) => String(Number(time)),
      },
      localization: { timeFormatter: (time: Time) => `第 ${Number(time)} 代` },
    });
    chartRef.current = chart;

    const bestScore = chart.addSeries(LineSeries, {
      color: ATTENTION.gold,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    // 适应度线条件在场（hasFitnessData）——series 集合随判定整体重建
    const bestFitness = hasFitnessData
      ? chart.addSeries(LineSeries, {
          color: POLARITY.up,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        })
      : null;
    const cumulativeBest = chart.addSeries(LineSeries, {
      color: withAlpha(POLARITY.up, 0.5),
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const avgScore = chart.addSeries(LineSeries, {
      color: SCORE_TREND_AVG_LINE_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    seriesRef.current = { bestScore, bestFitness, cumulativeBest, avgScore };

    // 悬停订阅挂初始化 effect（D1：与图表实例同生共死，数据 effect 重跑不
    // 重复订阅）。离图（time/point 任一 undefined）清态；反查未命中走防御态
    // 不更新。time=generation 承载值（genToTime 直承载），反查对象是过滤后
    // 序列（与绘制同源——被过滤点不进 setData，反查 props 原始 records 会
    // 显示画面上不存在的代）
    chart.subscribeCrosshairMove((param: MouseEventParams) => {
      if (param.time === undefined || param.point === undefined) {
        setHover(null);
        return;
      }
      const hit = safeDataRef.current.find(
        (d) => d.generation === Number(param.time),
      );
      if (!hit) return;
      setHover({ point: hit, x: param.point.x, y: param.point.y });
    });

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = EMPTY_SERIES;
      markersRef.current = null;
    };
  }, [hasData, canDraw, hasFitnessData]);

  useEffect(() => {
    if (!hasData || !canDraw || !chartRef.current) return;
    const { bestScore, bestFitness, cumulativeBest, avgScore } = seriesRef.current;
    if (!bestScore || !cumulativeBest || !avgScore) return;

    // value/time 面兜底：非法数值（NaN/Infinity/非数）dev 构建经库内部断言
    // throw 崩组件树、production 断言剥除后静默进数据模型——装配层剔除
    //（generation 脏值与 value 同炸，与 EquityLineChart timestamp 面同款
    // 纪律）；bestFitness null=断线 whitespace 语义承载，不滤点
    const safeData = data.filter(
      (d) =>
        Number.isFinite(d.generation) &&
        Number.isFinite(d.bestScore) &&
        Number.isFinite(d.avgScore) &&
        Number.isFinite(d.cumulativeBest) &&
        (d.bestFitness == null || Number.isFinite(d.bestFitness)),
    );
    // ref 通道：同一过滤后序列供订阅回调反查（与绘制同源）
    safeDataRef.current = safeData;

    bestScore.setData(
      safeData.map((d) => ({ time: genToTime(d.generation), value: d.bestScore })),
    );
    // fitness 缺代以 whitespace 断点承载（断线语义）
    bestFitness?.setData(
      safeData.map((d) =>
        d.bestFitness != null
          ? { time: genToTime(d.generation), value: d.bestFitness }
          : { time: genToTime(d.generation) },
      ),
    );
    cumulativeBest.setData(
      safeData.map((d) => ({ time: genToTime(d.generation), value: d.cumulativeBest })),
    );
    avgScore.setData(
      safeData.map((d) => ({ time: genToTime(d.generation), value: d.avgScore })),
    );

    // 双 priceLine 二选一（旧 markLine 归属随迁：目标线挂 bestScore、达标线挂 bestFitness）
    const targetSeries = hasFitnessData ? bestFitness : bestScore;
    const targetValue = hasFitnessData ? targetFitness : targetScore;
    if (targetSeries) {
      for (const line of targetSeries.priceLines()) targetSeries.removePriceLine(line);
      targetSeries.createPriceLine(
        hasFitnessData
          ? {
              price: targetValue,
              color: POLARITY.up,
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              title: "达标线",
            }
          : {
              price: targetValue,
              color: SCORE_TREND_TARGET_LINE_COLOR,
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              title: `目标 ${targetValue}`,
            },
      );
      // 目标线入值域（D2 随迁项）：库 autoScale 值域只按 series 数据计算，
      // priceLine 不参与——线值越出数据域时整条被裁不可见。在承载 series 上
      // 包装默认 autoscaleInfoProvider 把线值并入 min/max；线值是 props 动态
      // 值，本 effect 依赖含之，重跑时 applyOptions 换新闭包，不致陈旧。
      targetSeries.applyOptions({
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
          const res = original();
          if (res?.priceRange) {
            res.priceRange.minValue = Math.min(res.priceRange.minValue, targetValue);
            res.priceRange.maxValue = Math.max(res.priceRange.maxValue, targetValue);
          }
          return res;
        },
      });
    }

    // champion 菱形散点→circle marker（旧 symbolSize 8）；种群边界竖线族→
    // gold 方块+种群号 marker（lightweight 无竖线原语——D2 修订承载形）
    // markers 两数据源（championChanges/种群边界）独立于 safeData 构造，
    // generation 面各自补滤（同上纪律；脏 time 经库断言崩渲染）
    const championMarkers: SeriesMarker<Time>[] = championChanges
      .filter((c) => Number.isFinite(c.generation))
      .map(
        (c): SeriesMarker<Time> => ({
          time: genToTime(c.generation),
          position: "inBar",
          color: POLARITY.up,
          shape: "circle",
          size: 8,
        }),
      );
    const boundaryMarkers: SeriesMarker<Time>[] = data
      .filter((d) => d.isPopulationBoundary && Number.isFinite(d.generation))
      .map(
        (d): SeriesMarker<Time> => ({
          time: genToTime(d.generation),
          position: "aboveBar",
          color: ATTENTION.gold,
          shape: "square",
          size: 6,
          text: `P${d.population}`,
        }),
      );
    const markers = [...championMarkers, ...boundaryMarkers].sort(
      (a, b) => Number(a.time) - Number(b.time),
    );
    if (markersRef.current) {
      markersRef.current.setMarkers(markers);
    } else {
      markersRef.current = createSeriesMarkers(bestScore, markers);
    }

    // Y 域随迁近似：值域=series 数据+目标线（上方 provider 并入），余量以
    // scaleMargins 0.1 承载（旧显式 padding 的等价形）
    chartRef.current.priceScale("right").applyOptions({
      autoScale: true,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    });
    chartRef.current.timeScale().fitContent();
  }, [hasData, canDraw, hasFitnessData, data, championChanges, targetScore, targetFitness]);

  // 浮层定位（消费 charts/core/tooltipPosition 纯函数，锚点=param.point）：
  // 渲染后 measure 实测尺寸再计算——useLayoutEffect 在 paint 前完成定位更新，
  // 无闪烁；jsdom 下 offsetWidth 恒 0（宽高传反类缺陷归 e2e 真布局终审）
  useLayoutEffect(() => {
    if (!hover || !tooltipRef.current || !containerRef.current) {
      setTooltipPos(null);
      return;
    }
    setTooltipPos(
      computeTooltipPosition({
        anchor: { x: hover.x, y: hover.y },
        tooltip: {
          width: tooltipRef.current.offsetWidth,
          height: tooltipRef.current.offsetHeight,
        },
        container: {
          width: containerRef.current.offsetWidth,
          height: containerRef.current.offsetHeight,
        },
      }),
    );
  }, [hover]);

  if (!hasData) {
    return (
      <div
        className="flex h-[200px] items-center justify-center rounded"
        style={{ backgroundColor: CHART_VA.bg, color: CHART_VA.dim }}
      >
        <span className="text-xs">暂无趋势数据</span>
      </div>
    );
  }

  const lastRecord = data[data.length - 1];
  // 悬停浮层派生值（格式化与判定在渲染层，回调薄）：delta 差分/达标判定/
  // 多样性三档色——全部来自过滤后真实 ChartDataPoint 与 props.targetFitness
  const hoverDelta =
    hover?.point.prevBestScore != null
      ? hover.point.bestScore - hover.point.prevBestScore
      : undefined;
  const hoverFitnessMet =
    hover?.point.bestFitness != null && hover.point.bestFitness >= targetFitness;
  const hoverDiversityColor =
    hover?.point.diversity == null
      ? undefined
      : hover.point.diversity > 0.7
        ? TOOLTIP_TEXT_COLORS.diversityHigh
        : hover.point.diversity > 0.4
          ? TOOLTIP_TEXT_COLORS.diversityMid
          : TOOLTIP_TEXT_COLORS.diversityLow;

  return (
    <div
      className="rounded p-2"
      style={{ backgroundColor: CHART_VA.bg, border: `1px solid ${CHART_VA.line}` }}
    >
      <div
        className="h-[200px] w-full"
        ref={containerRef}
        data-testid="score-chart-container"
        style={{ position: "relative" }}
      >
        {hover && (
          <div
            ref={tooltipRef}
            data-testid="score-tooltip"
            style={{
              position: "absolute",
              left: tooltipPos?.left,
              top: tooltipPos?.top,
              zIndex: 10,
              pointerEvents: "none",
              // 旧 tooltip 外层样式随迁（§1.2 ScoreTrend 侧清单，A1 视觉近似面）
              backgroundColor: "rgba(13,17,23,0.95)",
              border: "1px solid rgba(51,65,85,0.5)",
              borderRadius: 8,
              color: TOOLTIP_TEXT_COLORS.head,
              fontSize: 12,
              padding: 8,
              minWidth: 160,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginBottom: 6,
              }}
            >
              <span
                style={{ fontWeight: 500, color: TOOLTIP_TEXT_COLORS.head }}
              >
                第 {hover.point.generation} 代
              </span>
              {hover.point.population > 1 && (
                <span style={{ color: TOOLTIP_TEXT_COLORS.headSub }}>
                  P{hover.point.population}
                </span>
              )}
            </div>
            <p style={{ margin: 0, color: ATTENTION.gold }}>
              综合得分: {hover.point.bestScore.toFixed(1)}
              {hoverDelta != null && hoverDelta !== 0 && (
                <span
                  style={{
                    marginLeft: 4,
                    color:
                      hoverDelta > 0
                        ? TOOLTIP_TEXT_COLORS.deltaUp
                        : TOOLTIP_TEXT_COLORS.deltaDown,
                  }}
                >
                  {hoverDelta > 0
                    ? `+${hoverDelta.toFixed(1)}`
                    : hoverDelta.toFixed(1)}
                </span>
              )}
            </p>
            {hover.point.bestFitness != null && (
              <p
                style={{
                  margin: 0,
                  // 未达标 70% 不透明（旧 b3 后缀语义）
                  color: hoverFitnessMet
                    ? POLARITY.up
                    : withAlpha(POLARITY.up, 0.7),
                }}
              >
                适应度: {hover.point.bestFitness.toFixed(2)}
                {hoverFitnessMet && (
                  <span
                    style={{ marginLeft: 4, fontSize: 10, fontWeight: 500 }}
                  >
                    达标
                  </span>
                )}
              </p>
            )}
            {hover.point.cumulativeBest !== hover.point.bestScore && (
              <p
                style={{
                  margin: 0,
                  color: withAlpha(POLARITY.up, 0.5),
                }}
              >
                累计最佳: {hover.point.cumulativeBest.toFixed(1)}
              </p>
            )}
            <p style={{ margin: 0, color: SCORE_TREND_AVG_LINE_COLOR }}>
              平均得分: {hover.point.avgScore.toFixed(1)}
            </p>
            {hover.point.avgTrades != null && (
              <p style={{ margin: 0, color: TOOLTIP_TEXT_COLORS.avgTrades }}>
                种群平均交易: {hover.point.avgTrades}
              </p>
            )}
            {hover.point.diversity != null && (
              <p style={{ margin: 0, color: hoverDiversityColor }}>
                种群多样性: {(hover.point.diversity * 100).toFixed(0)}%
              </p>
            )}
            {hover.point.stagnationCount > 0 && (
              <p style={{ margin: 0, color: TOOLTIP_TEXT_COLORS.stagnation }}>
                停滞: {hover.point.stagnationCount} 代未突破
              </p>
            )}
            {(hover.point.isPopulationBoundary || hover.point.isChampionChange) && (
              <p
                style={{
                  margin: "4px 0 0",
                  borderTop: `1px solid ${TOOLTIP_TEXT_COLORS.divider}`,
                  paddingTop: 4,
                  fontWeight: 500,
                  color: hover.point.isPopulationBoundary
                    ? TOOLTIP_TEXT_COLORS.newPopulation
                    : TOOLTIP_TEXT_COLORS.champion,
                }}
              >
                {hover.point.isPopulationBoundary ? "新种群开始" : "最优记录刷新"}
              </p>
            )}
          </div>
        )}
      </div>
      {/* Legend（文字色=va-dim；色块沿全局调色板） */}
      <div
        className="mt-1 flex items-center justify-center gap-4 text-[10px]"
        style={{ color: CHART_VA.dim }}>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-0.5 w-4"
            style={{ backgroundColor: ATTENTION.gold }}
          />
          综合得分
        </span>
        {hasFitnessData && (
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-0.5 w-4"
              style={{ backgroundColor: POLARITY.up }}
            />
            适应度
          </span>
        )}
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-0.5 w-4 border-t border-dashed"
            style={{ borderColor: withAlpha(POLARITY.up, 0.5) }}
          />
          累计最佳
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-0.5 w-4 border-t border-dashed"
            style={{ borderColor: SCORE_TREND_AVG_LINE_COLOR }}
          />
          平均得分
        </span>
        {hasFitnessData ? (
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-0.5 w-4 border-t border-dashed"
              style={{ borderColor: POLARITY.up }}
            />
            达标线
          </span>
        ) : (
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-0.5 w-4 border-t border-dashed"
              style={{ borderColor: SCORE_TREND_TARGET_LINE_COLOR }}
            />
            目标线
          </span>
        )}
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: POLARITY.up }}
          />
          最优更新
        </span>
      </div>
      {/* Summary stats（最优刷新=va-amber 强调系） */}
      <div className="mt-2 flex items-center justify-center gap-4 text-[10px]">
        <span style={{ color: CHART_VA.amber }}>
          最优刷新 {stats.totalChampionChanges} 次
        </span>
        <span
          style={{
            color:
              stats.maxStagnation > 10 ? CHART_VA.amber : CHART_VA.dim,
          }}
        >
          最长停滞 {stats.maxStagnation} 代
        </span>
        {lastRecord?.diversity != null && typeof lastRecord.diversity === "number" && (
          <span
            // 对质 1（t3r4）：多样性分档语义保留——琥珀终端风下收为两档
            //（原三档 emerald/amber/red 的「绿=好」在 va 域无对应色且多样性
            // 高低无好坏定论；<0.4 低多样性提示与「最长停滞」警示同 amber 通道）
            style={{ color: lastRecord.diversity <= 0.4 ? CHART_VA.amber : CHART_VA.dim }}
          >
            当前多样性 {(lastRecord.diversity * 100).toFixed(0)}%
          </span>
        )}
        {stats.populationCount > 1 && (
          <span style={{ color: CHART_VA.dim }}>
            {stats.populationCount} 个种群
          </span>
        )}
      </div>
    </div>
  );
}
