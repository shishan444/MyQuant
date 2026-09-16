/**
 * 进化得分趋势图（echarts）。
 *
 * 数据变换在 utils/evolutionChart.ts，option 构造在
 * charts/core/scoreTrendOption（纯函数，L1 直测）。
 */
import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/components/charts/core/echartsCore";
import { buildScoreTrendOption } from "@/components/charts/core/scoreTrendOption";
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
      <div className="flex h-[200px] items-center justify-center">
        <span className="text-xs text-slate-600">暂无趋势数据</span>
      </div>
    );
  }

  const lastRecord = data[data.length - 1];

  return (
    <div>
      <div className="h-[200px] w-full" ref={containerRef} />
      {/* Legend */}
      <div className="mt-1 flex items-center justify-center gap-4 text-[10px] text-slate-600">
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
      {/* Summary stats */}
      <div className="mt-2 flex items-center justify-center gap-4 text-[10px]">
        <span className="text-slate-500">
          最优刷新 {stats.totalChampionChanges} 次
        </span>
        <span className={stats.maxStagnation > 10 ? "text-orange-400" : "text-slate-500"}>
          最长停滞 {stats.maxStagnation} 代
        </span>
        {lastRecord?.diversity != null && typeof lastRecord.diversity === "number" && (
          <span className={
            lastRecord.diversity > 0.7
              ? "text-emerald-400/70"
              : lastRecord.diversity > 0.4
                ? "text-amber-400/70"
                : "text-red-400/70"
          }>
            当前多样性 {(lastRecord.diversity * 100).toFixed(0)}%
          </span>
        )}
        {stats.populationCount > 1 && (
          <span className="text-cyan-400/70">
            {stats.populationCount} 个种群
          </span>
        )}
      </div>
    </div>
  );
}
