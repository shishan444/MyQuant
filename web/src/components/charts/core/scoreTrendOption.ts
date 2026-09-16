/**
 * 进化得分趋势 option 构造（纯函数，L1 直测入口）。
 * 数据变换（transformChartData）在 utils/evolutionChart.ts，此处只做 option 装配。
 */
import type { ChartOption } from "./echartsCore";
import { CHART_PALETTE } from "./chartPalette";
import type { ChartDataPoint } from "@/utils/evolutionChart";

export interface ScoreTrendOptionInput {
  data: ChartDataPoint[];
  championChanges: ChartDataPoint[];
  boundaries: number[];
  targetScore: number;
  targetFitness?: number;
  hasFitnessData: boolean;
}

/** 单个数据点的 tooltip 富信息（内联样式，字段与迁移前 CustomTooltip 一致） */
function renderPointTooltip(point: ChartDataPoint, targetFitness: number): string {
  const delta = point.prevBestScore != null
    ? point.bestScore - point.prevBestScore
    : undefined;
  const diversityPct = point.diversity != null
    ? `${(point.diversity * 100).toFixed(0)}%`
    : null;
  const diversityColor = point.diversity! > 0.7
    ? "#34d399"
    : point.diversity! > 0.4
      ? "#fbbf24"
      : "#f87171";
  const fitnessMet = point.bestFitness != null && point.bestFitness >= targetFitness;

  const rows: string[] = [];
  rows.push(
    `<p style="margin:0;color:${CHART_PALETTE.scoreLine}">综合得分: ${point.bestScore.toFixed(1)}` +
      (delta != null && delta !== 0
        ? `<span style="margin-left:4px;color:${delta > 0 ? "#34d399" : "#f87171b3"}">` +
          `${delta > 0 ? `+${delta.toFixed(1)}` : delta.toFixed(1)}</span>`
        : "") +
      `</p>`,
  );
  if (point.bestFitness != null) {
    rows.push(
      `<p style="margin:0;color:${CHART_PALETTE.fitnessLine}${fitnessMet ? "" : "b3"}">适应度: ` +
        `${point.bestFitness.toFixed(2)}` +
        (fitnessMet ? `<span style="margin-left:4px;font-size:10px;font-weight:500">达标</span>` : "") +
        `</p>`,
    );
  }
  if (point.cumulativeBest !== point.bestScore) {
    rows.push(
      `<p style="margin:0;color:${CHART_PALETTE.cumulativeLine}">累计最佳: ${point.cumulativeBest.toFixed(1)}</p>`,
    );
  }
  rows.push(
    `<p style="margin:0;color:${CHART_PALETTE.avgLine}">平均得分: ${point.avgScore.toFixed(1)}</p>`,
  );
  if (point.avgTrades != null) {
    rows.push(`<p style="margin:0;color:#94a3b8">种群平均交易: ${point.avgTrades}</p>`);
  }
  if (diversityPct != null) {
    rows.push(
      `<p style="margin:0;color:${diversityColor}">种群多样性: ${diversityPct}</p>`,
    );
  }
  if (point.stagnationCount > 0) {
    rows.push(
      `<p style="margin:0;color:#fb923cb3">停滞: ${point.stagnationCount} 代未突破</p>`,
    );
  }
  if (point.isPopulationBoundary) {
    rows.push(
      `<p style="margin:4px 0 0;border-top:1px solid rgba(51,65,85,0.3);padding-top:4px;` +
        `color:#22d3eecc;font-weight:500">新种群开始</p>`,
    );
  } else if (point.isChampionChange) {
    rows.push(
      `<p style="margin:4px 0 0;border-top:1px solid rgba(51,65,85,0.3);padding-top:4px;` +
        `color:#34d399;font-weight:500">最优记录刷新</p>`,
    );
  }

  return (
    `<div style="min-width:160px">` +
    `<div style="display:flex;justify-content:space-between;margin-bottom:6px">` +
    `<span style="font-weight:500;color:#cbd5e1">第 ${point.generation} 代</span>` +
    (point.population > 1 ? `<span style="color:#64748b">P${point.population}</span>` : "") +
    `</div>${rows.join("")}</div>`
  );
}

export function buildScoreTrendOption({
  data,
  championChanges,
  boundaries,
  targetScore,
  targetFitness = 1.0,
  hasFitnessData,
}: ScoreTrendOptionInput): ChartOption {
  // Y 轴值域：覆盖全部序列值与目标线，向下留一档余量
  const allY = data.flatMap((d) => {
    const vals = [d.bestScore, d.avgScore];
    if (d.bestFitness != null) vals.push(d.bestFitness);
    return vals;
  });
  if (hasFitnessData) allY.push(targetFitness);
  else allY.push(targetScore);
  const dataMin = Math.min(...allY);
  const dataMax = Math.max(...allY);
  const yMin = Math.floor(Math.min(dataMin, hasFitnessData ? targetFitness : targetScore) / 5) * 5 - 5;
  const yMax = Math.ceil((dataMax + 5) / 5) * 5;

  const baseLine = {
    color: CHART_PALETTE.axisLabel,
    fontSize: 11,
  };

  return {
    animation: false,
    grid: { top: 5, right: 10, left: -10, bottom: 5, containLabel: true },
    xAxis: {
      type: "category",
      data: data.map((d) => String(d.generation)),
      axisLabel: baseLine,
      axisLine: { lineStyle: { color: "rgba(100,116,139,0.2)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      min: yMin,
      max: yMax,
      axisLabel: baseLine,
      splitLine: {
        lineStyle: { color: CHART_PALETTE.gridLine, type: [3, 3] },
      },
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(13,17,23,0.95)",
      borderColor: "rgba(51,65,85,0.5)",
      borderWidth: 1,
      padding: 8,
      textStyle: { color: "#cbd5e1", fontSize: 12 },
      extraCssText: "border-radius:8px;",
      formatter: (params) => {
        const first = Array.isArray(params) ? params[0] : params;
        const point = data[first.dataIndex];
        if (!point) return "";
        return renderPointTooltip(point, targetFitness);
      },
    },
    series: [
      {
        name: "bestScore",
        type: "line",
        data: data.map((d) => d.bestScore),
        showSymbol: false,
        smooth: true,
        lineStyle: { color: CHART_PALETTE.scoreLine, width: 2 },
        emphasis: { scale: false },
        markLine: {
          symbol: "none",
          silent: true,
          label: { position: "end" as const, fontSize: 10 },
          data: [
            ...(hasFitnessData
              ? []
              : [
                  {
                    yAxis: targetScore,
                    lineStyle: {
                      color: CHART_PALETTE.targetLine,
                      type: [6, 3] as [number, number],
                      width: 1,
                    },
                    label: {
                      formatter: `目标 ${targetScore}`,
                      color: CHART_PALETTE.targetLine,
                    },
                  },
                ]),
            ...boundaries.map((gen) => ({
              xAxis: String(gen),
              lineStyle: {
                color: CHART_PALETTE.boundary,
                type: [4, 4] as [number, number],
                width: 1,
              },
              label: { show: false },
            })),
          ],
        },
      },
      ...(hasFitnessData
        ? [
            {
              name: "bestFitness",
              type: "line" as const,
              data: data.map((d) => d.bestFitness ?? null),
              showSymbol: false,
              smooth: true,
              lineStyle: { color: CHART_PALETTE.fitnessLine, width: 2.5 },
              emphasis: { scale: false },
              markLine: {
                symbol: "none",
                silent: true,
                data: [
                  {
                    yAxis: targetFitness,
                    lineStyle: {
                      color: CHART_PALETTE.fitnessLine,
                      type: [6, 3] as [number, number],
                      width: 1.5,
                    },
                    label: {
                      formatter: "达标线",
                      position: "end" as const,
                      color: CHART_PALETTE.fitnessLine,
                      fontSize: 10,
                    },
                  },
                ],
              },
            },
          ]
        : []),
      {
        name: "cumulativeBest",
        type: "line",
        data: data.map((d) => d.cumulativeBest),
        showSymbol: false,
        smooth: true,
        lineStyle: { color: CHART_PALETTE.cumulativeLine, width: 1, type: [2, 3] },
        emphasis: { scale: false },
      },
      {
        name: "avgScore",
        type: "line",
        data: data.map((d) => d.avgScore),
        showSymbol: false,
        smooth: true,
        lineStyle: { color: CHART_PALETTE.avgLine, width: 1, type: [4, 2] },
        emphasis: { scale: false },
      },
      {
        name: "championMarker",
        type: "scatter",
        data: championChanges.map((c) => [String(c.generation), c.bestScore]),
        symbol: "diamond",
        symbolSize: 8,
        itemStyle: { color: CHART_PALETTE.champion },
      },
    ],
  };
}
