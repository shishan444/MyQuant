/**
 * 进化得分趋势图（echarts）。
 *
 * 数据变换在 utils/evolutionChart.ts，option 构造在
 * charts/core/scoreTrendOption（纯函数，L1 直测）。
 * 容器底色=CHART_VA（fe-research-execution-domain 3.1——05 进化图表
 * 琥珀终端风：va-bg 底+va-line 描边+va-amber 最优强调系；线色仍走
 * 全局 CHART_PALETTE，蜡烛恒等规则不适用本图非 K 线）。
 */
import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/components/charts/core/echartsCore";
import { buildScoreTrendOption } from "@/components/charts/core/scoreTrendOption";
import { CHART_VA } from "@/theme/tokens";
import type { EvolutionHistoryRecord } from "@/types/api";
import { transformChartData, type ChartTransformResult } from "@/utils/evolutionChart";

interface ScoreTrendChartProps {
  records: EvolutionHistoryRecord[];
  targetScore: number;
  targetFitness?: number;
}

export function ScoreTrendChart({
  records,
  targetScore,
  targetFitness = 1.0,
}: ScoreTrendChartProps) {
  const { data, championChanges, boundaries, stats }: ChartTransformResult = useMemo(
    () => transformChartData(records),
    [records],
  );

  const hasFitnessData = data.some((d) => d.bestFitness != null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const hasData = data.length > 0;

  useEffect(() => {
    if (!hasData || !containerRef.current) return;
    const chart = echarts.init(containerRef.current);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [hasData]);

  useEffect(() => {
    if (!hasData || !chartRef.current) return;
    chartRef.current.setOption(
      buildScoreTrendOption({
        data,
        championChanges,
        boundaries,
        targetScore,
        targetFitness,
        hasFitnessData,
      }),
      { notMerge: true },
    );
  }, [hasData, data, championChanges, boundaries, targetScore, targetFitness, hasFitnessData]);

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
          <span className="inline-block h-0.5 w-4 bg-amber-400" />
          综合得分
        </span>
        {hasFitnessData && (
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 bg-emerald-500" />
            适应度
          </span>
        )}
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-4 border-t border-dashed border-emerald-400/50" />
          累计最佳
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-4 border-t border-dashed border-purple-400/40" />
          平均得分
        </span>
        {hasFitnessData ? (
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 border-t border-dashed border-emerald-500" />
            达标线
          </span>
        ) : (
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 border-t border-dashed border-slate-400" />
            目标线
          </span>
        )}
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rotate-45 bg-emerald-500" />
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
