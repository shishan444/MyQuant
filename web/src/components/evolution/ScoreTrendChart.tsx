/**
 * 进化得分趋势图（lightweight-charts 单库形态，fe-chart-single-lib 组 3）。
 *
 * 数据变换在 utils/evolutionChart.ts（不动）；装配按 design D2 修订清单：
 * 四 LineSeries（bestScore 金/bestFitness 青条件在场/cumulativeBest 半透明
 * 青点线恒在/avgScore 紫蚂蚁线）+双 priceLine 二选一（无 fitness→目标线/
 * 有→达标线）+champion 与种群边界 markers（lightweight 无竖线原语，边界
 * 竖线族改 marker 承载）。容器底色=CHART_VA（琥珀终端风，形制沿旧）。
 * jsdom 降级：内建 canvas 可用性检测，jsdom 下跳过初始化保留容器与空态。
 */
import { useEffect, useMemo, useRef } from "react";
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
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { withAlpha, SCORE_TREND_AVG_LINE_COLOR, SCORE_TREND_TARGET_LINE_COLOR, SCORE_TREND_AXIS_LABEL_COLOR, SCORE_TREND_GRID_LINE_COLOR } from "@/components/charts/core/chartThemes";
import { CHART_VA, POLARITY, ATTENTION } from "@/theme/tokens";
import type { EvolutionHistoryRecord } from "@/types/api";
import { transformChartData, type ChartTransformResult } from "@/utils/evolutionChart";

// 系列色归宿（design D5）：scoreLine/fitnessLine/champion 直接 import tokens；
// avgLine/targetLine/axisLabel/grid 四字面已迁 chartThemes 常量区（组 4.1 归宿，
// 字面值原样），本组件经 import 引用。

interface ScoreTrendChartProps {
  records: EvolutionHistoryRecord[];
  targetScore: number;
  targetFitness?: number;
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

  return (
    <div
      className="rounded p-2"
      style={{ backgroundColor: CHART_VA.bg, border: `1px solid ${CHART_VA.line}` }}
    >
      <div className="h-[200px] w-full" ref={containerRef} />
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
