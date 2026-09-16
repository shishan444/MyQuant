/**
 * Tests for ScoreTrendChart (echarts): L1 option 纯函数直测 + L2 组件生命周期（mock echarts/core）。
 * transformChartData 本身已有独立 L1（evolutionChart.test.ts），此处只测 option 构造与薄壳。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("echarts/core", () => {
  const chart = { setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() };
  const init = vi.fn(() => chart);
  return { init, use: vi.fn() };
});

import * as echartsCore from "echarts/core";
import { ScoreTrendChart } from "@/components/evolution/ScoreTrendChart";
import { buildScoreTrendOption } from "@/components/charts/core/scoreTrendOption";
import { transformChartData } from "@/utils/evolutionChart";
import { CHART_PALETTE } from "@/components/charts/core/chartPalette";
import type { EvolutionHistoryRecord } from "@/types/api";

const initMock = vi.mocked(echartsCore.init);
const chartMock = initMock() as unknown as { setOption: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> };

function makeRecord(over: Partial<EvolutionHistoryRecord> = {}): EvolutionHistoryRecord {
  return {
    generation: 1,
    best_score: 50,
    avg_score: 40,
    created_at: "2025-01-01T00:00:00Z",
    ...over,
  };
}

// 两代无适应度 → 目标线形态
const noFitnessRecords = [
  makeRecord({ generation: 1, best_score: 50 }),
  makeRecord({ generation: 2, best_score: 55 }),
];
// 三代带适应度与诊断 → 达标线 + 种群边界 + 停滞形态
const fitnessRecords = [
  makeRecord({ generation: 1, best_score: 50, best_fitness: 0.6, top3_summary: "pop=1|diag={\"diversity\":0.8,\"avg_trades\":3}" }),
  makeRecord({ generation: 2, best_score: 58, best_fitness: 0.9, top3_summary: "pop=1" }),
  makeRecord({ generation: 3, best_score: 52, best_fitness: 1.1, top3_summary: "pop=2|diag={\"diversity\":0.5}" }),
];

function buildFromRecords(records: EvolutionHistoryRecord[], targetScore = 60) {
  const { data, championChanges, boundaries } = transformChartData(records);
  const hasFitnessData = data.some((d) => d.bestFitness != null);
  return buildScoreTrendOption({
    data,
    championChanges,
    boundaries,
    targetScore,
    targetFitness: 1.0,
    hasFitnessData,
  });
}

interface MarkLineItem {
  yAxis?: number;
  xAxis?: string;
  label?: { formatter?: string };
  lineStyle?: { color: string };
}

interface SeriesLike {
  type?: string;
  name?: string;
  symbol?: string;
  data?: unknown;
  markLine?: { data: MarkLineItem[] };
}

// Arrayable 收窄为已知数组（组件恒返回数组形态）
const seriesOf = (opt: ReturnType<typeof buildScoreTrendOption>) =>
  opt.series as unknown as SeriesLike[];

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// L1: buildScoreTrendOption
// ---------------------------------------------------------------------------

describe("buildScoreTrendOption", () => {
  it("lays out 4 line series + champion scatter when fitness data present", () => {
    const option = buildFromRecords(fitnessRecords);
    const lines = seriesOf(option).filter((s) => s.type === "line");
    const scatters = seriesOf(option).filter((s) => s.type === "scatter");
    expect(lines.map((s) => s.name)).toEqual([
      "bestScore",
      "bestFitness",
      "cumulativeBest",
      "avgScore",
    ]);
    expect(scatters).toHaveLength(1);
    expect(scatters[0].symbol).toBe("diamond");
  });

  it("drops bestFitness series and uses target-score line when no fitness data", () => {
    const option = buildFromRecords(noFitnessRecords, 60);
    const lines = seriesOf(option).filter((s) => s.type === "line");
    expect(lines.map((s) => s.name)).toEqual([
      "bestScore",
      "cumulativeBest",
      "avgScore",
    ]);
    const best = lines[0];
    const targetLine = best.markLine!.data.find((d) => d.yAxis === 60)!;
    expect(targetLine.label!.formatter).toBe("目标 60");
    expect(targetLine.lineStyle!.color).toBe(CHART_PALETTE.targetLine);
  });

  it("adds fitness target markLine (达标线) when fitness data present", () => {
    const option = buildFromRecords(fitnessRecords);
    const fitnessSeries = seriesOf(option).find((s) => s.name === "bestFitness")!;
    const markData = fitnessSeries.markLine!.data;
    expect(markData).toHaveLength(1);
    expect(markData[0].yAxis).toBe(1.0);
    expect(markData[0].label!.formatter).toBe("达标线");
    expect(markData[0].lineStyle!.color).toBe(CHART_PALETTE.fitnessLine);
  });

  it("adds vertical boundary markLine at population change generation", () => {
    const option = buildFromRecords(fitnessRecords);
    const bestSeries = seriesOf(option).find((s) => s.name === "bestScore")!;
    const boundary = bestSeries.markLine!.data.find((d) => d.xAxis != null)!;
    expect(boundary.xAxis).toBe("3");
    expect(boundary.lineStyle!.color).toBe(CHART_PALETTE.boundary);
  });

  it("computes y-axis domain from all series values and target", () => {
    const option = buildFromRecords(fitnessRecords);
    // 值域 {50,58,52,bestFit 0.6/0.9/1.1,avg 40, target 1.0} → min 0.6 max 58
    // yMin = floor(0.6/5)*5-5 = -5, yMax = ceil(63/5)*5 = 65
    const yAxis = option.yAxis as { min: number; max: number };
    expect(yAxis.min).toBe(-5);
    expect(yAxis.max).toBe(65);
  });

  it("scatter carries champion-change points as [generation, score]", () => {
    const option = buildFromRecords(fitnessRecords);
    const { championChanges } = transformChartData(fitnessRecords);
    const scatter = seriesOf(option).find((s) => s.type === "scatter")!;
    expect(scatter.data).toEqual(championChanges.map((c) => [String(c.generation), c.bestScore]));
  });

  it("tooltip renders rich per-generation info", () => {
    const option = buildFromRecords(fitnessRecords);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter as (p: unknown) => string;

    // 第 3 代：种群边界 + 停滞
    const boundaryHtml = formatter([{ dataIndex: 2 }]);
    expect(boundaryHtml).toContain("第 3 代");
    expect(boundaryHtml).toContain("综合得分: 52.0");
    expect(boundaryHtml).toContain("适应度: 1.10");
    expect(boundaryHtml).toContain("达标");
    expect(boundaryHtml).toContain("种群多样性: 50%");
    expect(boundaryHtml).toContain("新种群开始");

    // 第 2 代：最优刷新
    const championHtml = formatter([{ dataIndex: 1 }]);
    expect(championHtml).toContain("综合得分: 58.0");
    expect(championHtml).toContain("+8.0");
    expect(championHtml).toContain("最优记录刷新");
    expect(championHtml).not.toContain("新种群开始");
  });

  it("tooltip shows stagnation when score declines within a population", () => {
    // 无边界回落序列：50 → 58 → 52（停滞 1）
    const records = [
      makeRecord({ generation: 1, best_score: 50 }),
      makeRecord({ generation: 2, best_score: 58 }),
      makeRecord({ generation: 3, best_score: 52 }),
    ];
    const option = buildFromRecords(records);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter as (p: unknown) => string;
    const html = formatter([{ dataIndex: 2 }]);
    expect(html).toContain("停滞: 1 代未突破");
    expect(html).not.toContain("新种群开始");
  });

  it("tooltip falls back to empty string for out-of-range index", () => {
    const option = buildFromRecords(fitnessRecords);
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter as (p: unknown) => string;
    expect(formatter([{ dataIndex: 99 }])).toBe("");
  });
});

// ---------------------------------------------------------------------------
// L2: component lifecycle (mock echarts/core)
// ---------------------------------------------------------------------------

describe("ScoreTrendChart component", () => {
  it("shows empty state without chart init when no records", () => {
    render(<ScoreTrendChart records={[]} targetScore={60} />);
    expect(screen.getByText("暂无趋势数据")).toBeInTheDocument();
    expect(initMock).not.toHaveBeenCalled();
  });

  it("inits chart and renders legend and stats", () => {
    render(
      <ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />,
    );
    expect(initMock).toHaveBeenCalledTimes(1);
    expect(chartMock.setOption).toHaveBeenCalledTimes(1);
    // legend（有适应度数据 → 适应度+达标线）
    expect(screen.getByText("综合得分")).toBeInTheDocument();
    expect(screen.getByText("适应度")).toBeInTheDocument();
    expect(screen.getByText("达标线")).toBeInTheDocument();
    // stats：gen1/gen2 两次刷新；gen3 虽得分回落但种群边界重置停滞 → 0
    expect(screen.getByText(/最优刷新 2 次/)).toBeInTheDocument();
    expect(screen.getByText(/最长停滞 0 代/)).toBeInTheDocument();
    expect(screen.getByText(/2 个种群/)).toBeInTheDocument();
  });

  it("shows 目标线 legend instead when no fitness data", () => {
    render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    expect(screen.getByText("目标线")).toBeInTheDocument();
    expect(screen.queryByText("适应度")).not.toBeInTheDocument();
  });

  it("re-sets option without merge when records change", () => {
    const { rerender } = render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    rerender(<ScoreTrendChart records={fitnessRecords} targetScore={60} />);
    const lastCall = chartMock.setOption.mock.calls.at(-1)!;
    expect(lastCall[1]).toEqual({ notMerge: true });
    const seriesNames = (lastCall[0].series as unknown as SeriesLike[]).map((s) => s.name);
    expect(seriesNames).toContain("bestFitness");
  });

  it("disposes chart on unmount", () => {
    const { unmount } = render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    unmount();
    expect(chartMock.dispose).toHaveBeenCalled();
  });
});
