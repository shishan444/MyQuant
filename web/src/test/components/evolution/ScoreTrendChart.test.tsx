/**
 * ScoreTrendChart 装配锚（fe-chart-single-lib 组 3）——断言走库 API 数据面
 * （addSeries 色与线宽/setData 点数/createPriceLine 条件显隐/marker 序列），
 * lightweight-charts 以 fake registry 承载调用序列（形制对齐 KlineChart.test）。
 * transformChartData 独立 L1 在 evolutionChart.test.ts，此处不重测。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("lightweight-charts", () => {
  const registry = { charts: [] as any[] };
  const mkChart = () => {
    const chart: any = {
      series: [] as any[],
      timeScale: vi.fn(() => ({ fitContent: vi.fn(), applyOptions: vi.fn() })),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      removeSeries: vi.fn(),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      addSeries: vi.fn((def: { _type?: string }, options: unknown) => {
        const priceLines: any[] = [];
        const s: any = {
          seriesType: def?._type ?? "?",
          options,
          setData: vi.fn(),
          applyOptions: vi.fn(),
          priceLines: vi.fn(() => priceLines),
          createPriceLine: vi.fn((o: unknown) => {
            const line = { options: o };
            priceLines.push(line);
            return line;
          }),
          removePriceLine: vi.fn((line: unknown) => {
            const i = priceLines.indexOf(line);
            if (i >= 0) priceLines.splice(i, 1);
          }),
        };
        chart.series.push(s);
        return s;
      }),
    };
    registry.charts.push(chart);
    return chart;
  };
  return {
    __registry: registry,
    createChart: vi.fn(mkChart),
    createSeriesMarkers: vi.fn((series: { markers: unknown }, markers: unknown[]) => {
      series.markers = markers;
      return { setMarkers: vi.fn((m: unknown[]) => { series.markers = m; }) };
    }),
    LineSeries: { _type: "Line" },
    ColorType: { Solid: "solid", VerticalGradient: "verticalGradient" },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
    CrosshairMode: { Normal: 0, Magnet: 1, Hidden: 2 },
  };
});

import * as lightweightCharts from "lightweight-charts";
import { ScoreTrendChart } from "@/components/evolution/ScoreTrendChart";
import {
  withAlpha,
  SCORE_TREND_AVG_LINE_COLOR,
  SCORE_TREND_TARGET_LINE_COLOR,
} from "@/components/charts/core/chartThemes";
import { POLARITY, ATTENTION } from "@/theme/tokens";
import type { EvolutionHistoryRecord } from "@/types/api";

const createChartMock = vi.mocked(lightweightCharts.createChart);
const createSeriesMarkersMock = vi.mocked(lightweightCharts.createSeriesMarkers);
const registry = (lightweightCharts as unknown as { __registry: { charts: any[] } }).__registry;

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
// 三代带适应度与诊断 → 达标线 + 种群边界（gen3 pop 1→2）形态
const fitnessRecords = [
  makeRecord({ generation: 1, best_score: 50, best_fitness: 0.6, top3_summary: "pop=1|diag={\"diversity\":0.8,\"avg_trades\":3}" }),
  makeRecord({ generation: 2, best_score: 58, best_fitness: 0.9, top3_summary: "pop=1" }),
  makeRecord({ generation: 3, best_score: 52, best_fitness: 1.1, top3_summary: "pop=2|diag={\"diversity\":0.5}" }),
];

/** 首个 chart 的 series 面板（addSeries 调用序=装配序） */
function seriesPanel() {
  const chart = registry.charts[0];
  if (!chart) throw new Error("chart not created");
  return chart.series as Array<{
    options: { color?: string; lineWidth?: number; lineStyle?: number };
    setData: ReturnType<typeof vi.fn>;
    createPriceLine: ReturnType<typeof vi.fn>;
    markers?: unknown;
  }>;
}

beforeEach(() => {
  registry.charts.length = 0;
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// 装配锚：四 series 色与线宽 / 双 priceLine 条件显隐 / marker 序列
// ---------------------------------------------------------------------------

describe("ScoreTrendChart 装配（fake registry）", () => {
  // canDraw 为 getContext("2d") 调用判空（C-2 修复形制）——jsdom 原生返回 null
  // 会让装配路径不触发；装配锚须还原浏览器形制（2d 上下文非空）才走到 createChart
  let ctxSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    ctxSpy = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as CanvasRenderingContext2D);
  });
  afterEach(() => ctxSpy.mockRestore());

  it("creates 4 line series with palette/width anchors when fitness data present", () => {
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    expect(series).toHaveLength(4);
    // bestScore 金 2px 实线
    expect(series[0].options).toMatchObject({ color: ATTENTION.gold, lineWidth: 2 });
    // bestFitness 青 2px 实线（条件在场；LineWidth 合法域 1|2|3|4）
    expect(series[1].options).toMatchObject({ color: POLARITY.up, lineWidth: 2 });
    // cumulativeBest 半透明青 1px 点线（恒在）
    expect(series[2].options).toMatchObject({
      color: withAlpha(POLARITY.up, 0.5),
      lineWidth: 1,
      lineStyle: 1, // LineStyle.Dotted
    });
    // avgScore 紫 40% 1px 蚂蚁线
    expect(series[3].options).toMatchObject({
      color: "rgba(168,85,247,0.4)",
      lineWidth: 1,
      lineStyle: 2, // LineStyle.Dashed
    });
    // setData 点数=代数零抽样（每条 series 全量承载）
    for (const s of series) {
      expect(s.setData).toHaveBeenCalledTimes(1);
      expect(s.setData.mock.calls[0][0]).toHaveLength(3);
    }
  });

  it("creates 3 series without bestFitness when no fitness data", () => {
    render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    const series = seriesPanel();
    expect(series).toHaveLength(3);
    expect(series.map((s) => s.options.color)).toEqual([
      ATTENTION.gold,
      withAlpha(POLARITY.up, 0.5),
      "rgba(168,85,247,0.4)",
    ]);
  });

  it("places 达标线 priceLine on bestFitness series when fitness data present", () => {
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    expect(series[1].createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ price: 1.0, color: POLARITY.up, lineWidth: 1, title: "达标线" }),
    );
    // 二选一：bestScore series 无目标线
    expect(series[0].createPriceLine).not.toHaveBeenCalled();
  });

  it("places 目标线 priceLine on bestScore series when no fitness data", () => {
    render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    const series = seriesPanel();
    expect(series[0].createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ price: 60, color: "#94A3B8", lineWidth: 1, title: "目标 60" }),
    );
  });

  it("carries champion circles and boundary squares as ordered markers", () => {
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    expect(createSeriesMarkersMock).toHaveBeenCalledTimes(1);
    expect(createSeriesMarkersMock.mock.calls[0][0]).toBe(series[0]);
    const markers = createSeriesMarkersMock.mock.calls[0][1] as Array<{
      time: number;
      position: string;
      color: string;
      shape: string;
      size: number;
      text?: string;
    }>;
    // champion=gen1/gen2 circle POLARITY.up；boundary=gen3 square gold P2；按 time 升序
    expect(markers).toHaveLength(3);
    expect(markers.map((m) => m.time)).toEqual([1, 2, 3]);
    expect(markers[0]).toMatchObject({ shape: "circle", color: POLARITY.up, position: "inBar", size: 8 });
    expect(markers[2]).toMatchObject({ shape: "square", color: ATTENTION.gold, position: "aboveBar", text: "P2" });
  });

  it("rebuilds chart (series set changes) when fitness presence flips on rerender", () => {
    const { rerender } = render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    rerender(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    expect(registry.charts).toHaveLength(2);
    expect(registry.charts[0].remove).toHaveBeenCalled();
    expect(registry.charts[1].series).toHaveLength(4);
  });

  it("removes chart on unmount", () => {
    const { unmount } = render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    unmount();
    expect(registry.charts[0].remove).toHaveBeenCalled();
  });

  // CARD-03（security S-1）：value 面有限性兜底——四路 setData 前整点过滤
  // （谓词=三 value 列有限性合取+bestFitness null 豁免），脏点剔除不崩、
  // 余点照常；null（断线语义）与 NaN（非法值）分叉处置是谓词核心语义
  it("filters non-finite best_score records（NaN 整点剔除，四路同步）", () => {
    const dirty = [
      makeRecord({ generation: 1, best_score: 50, best_fitness: 0.6 }),
      makeRecord({ generation: 2, best_score: 58, best_fitness: 0.9 }),
      makeRecord({ generation: 3, best_score: Number.NaN, best_fitness: 1.1 }),
    ];
    render(<ScoreTrendChart records={dirty} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    expect(series).toHaveLength(4);
    for (const s of series) {
      expect(s.setData).toHaveBeenCalledTimes(1);
      expect(s.setData.mock.calls[0][0]).toHaveLength(2);
    }
  });

  it("keeps best_fitness=null points as whitespace 断线（null 不剔点）", () => {
    // 后端违约形制注入：类型面已把后端 null 收窄为可选（types/api.ts:173/:178），
    // 运行时直传 null 模拟真实到达的断线代——cast 不改变注入值本身
    const backendNull = null as unknown as number;
    const withGap = [
      makeRecord({ generation: 1, best_score: 50, best_fitness: 0.6 }),
      makeRecord({ generation: 2, best_score: 55, best_fitness: backendNull }),
      makeRecord({ generation: 3, best_score: 52, best_fitness: 1.1 }),
    ];
    render(<ScoreTrendChart records={withGap} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    // null=断线语义承载：点保留（四路 3 点），bestFitness 路该点无 value
    for (const s of series) {
      expect(s.setData.mock.calls[0][0]).toHaveLength(3);
    }
    const fitnessPoints = series[1].setData.mock.calls[0][0] as Array<Record<string, unknown>>;
    expect(fitnessPoints[1]).not.toHaveProperty("value");
    expect(fitnessPoints[0]).toHaveProperty("value", 0.6);
  });

  it("filters best_fitness=NaN records（NaN 非断线语义，整点剔除）", () => {
    const dirty = [
      makeRecord({ generation: 1, best_score: 50, best_fitness: 0.6 }),
      makeRecord({ generation: 2, best_score: 55, best_fitness: Number.NaN }),
      makeRecord({ generation: 3, best_score: 52, best_fitness: 1.1 }),
    ];
    render(<ScoreTrendChart records={dirty} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    for (const s of series) {
      expect(s.setData.mock.calls[0][0]).toHaveLength(2);
    }
  });
});

// ---------------------------------------------------------------------------
// 组件面：空态 / Legend / Summary stats（文案沿旧锚）
// ---------------------------------------------------------------------------

describe("ScoreTrendChart component", () => {
  it("shows empty state without chart init when no records", () => {
    render(<ScoreTrendChart records={[]} targetScore={60} />);
    expect(screen.getByText("暂无趋势数据")).toBeInTheDocument();
    expect(createChartMock).not.toHaveBeenCalled();
  });

  it("数据充足 + jsdom 无 canvas：降级跳过 init，legend/summary 照常在场", () => {
    // 本 describe 不 stub getContext（jsdom 原生返回 null → canDraw=false 降级路径）；
    // 显式锚定降级契约：不初始化图表但不吞掉组件的 DOM 面（D3 机制在 ScoreTrend 侧）
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    expect(createChartMock).not.toHaveBeenCalled();
    expect(screen.getByText("综合得分")).toBeInTheDocument();
    expect(screen.getByText(/最优刷新 2 次/)).toBeInTheDocument();
  });

  it("renders legend and stats for fitness form", () => {
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    // legend（有适应度数据 → 适应度+达标线）
    expect(screen.getByText("综合得分")).toBeInTheDocument();
    expect(screen.getByText("适应度")).toBeInTheDocument();
    expect(screen.getByText("达标线")).toBeInTheDocument();
    // stats：gen1/gen2 两次刷新；gen3 虽得分回落但种群边界重置停滞 → 0
    expect(screen.getByText(/最优刷新 2 次/)).toBeInTheDocument();
    expect(screen.getByText(/最长停滞 0 代/)).toBeInTheDocument();
    expect(screen.getByText(/2 个种群/)).toBeInTheDocument();
  });

  it("renders legend swatches in tokens 单源引用形（CARD-01：七色块与 series 实际色一致）", () => {
    // 图例色块=标签项容器（getByText 命中 flex span）的首子元素；断言值=
    // tokens/chartThemes 引用（与对应 series 装配色同源——错位回退必红）
    const swatch = (label: string) =>
      screen.getByText(label).firstElementChild as HTMLElement;
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />);
    expect(swatch("综合得分")).toHaveStyle({ backgroundColor: ATTENTION.gold });
    expect(swatch("适应度")).toHaveStyle({ backgroundColor: POLARITY.up });
    expect(swatch("累计最佳")).toHaveStyle({ borderColor: withAlpha(POLARITY.up, 0.5) });
    expect(swatch("平均得分")).toHaveStyle({ borderColor: SCORE_TREND_AVG_LINE_COLOR });
    expect(swatch("达标线")).toHaveStyle({ borderColor: POLARITY.up });
    expect(swatch("最优更新")).toHaveStyle({ backgroundColor: POLARITY.up });
  });

  it("shows 目标线 legend instead when no fitness data", () => {
    render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    expect(screen.getByText("目标线")).toBeInTheDocument();
    expect(screen.queryByText("适应度")).not.toBeInTheDocument();
    // 目标线色块同入单源锚（CARD-01 两处同值项一并改引用形）
    const swatch = screen.getByText("目标线").firstElementChild as HTMLElement;
    expect(swatch).toHaveStyle({ borderColor: SCORE_TREND_TARGET_LINE_COLOR });
  });
});
