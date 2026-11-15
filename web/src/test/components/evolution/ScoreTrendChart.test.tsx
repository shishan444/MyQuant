/**
 * ScoreTrendChart 装配锚（fe-chart-single-lib 组 3）——断言走库 API 数据面
 * （addSeries 色与线宽/setData 点数/createPriceLine 条件显隐/marker 序列），
 * lightweight-charts 以 fake registry 承载调用序列（形制对齐 KlineChart.test）。
 * transformChartData 独立 L1 在 evolutionChart.test.ts，此处不重测。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

// tooltipPosition mock 转发（S-12 定位接线用）：默认透传真实实现（协作者由
// tooltipPosition.test.ts 直测独立验证），受控返回仅 S-12 内设置
vi.mock("@/components/charts/core/tooltipPosition", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/components/charts/core/tooltipPosition")
  >();
  return { ...actual, computeTooltipPosition: vi.fn(actual.computeTooltipPosition) };
});

vi.mock("lightweight-charts", () => {
  const registry = {
    charts: [] as any[],
    crosshairHandlers: [] as Array<(p: unknown) => void>,
  };
  const mkChart = () => {
    const chart: any = {
      series: [] as any[],
      timeScale: vi.fn(() => ({ fitContent: vi.fn(), applyOptions: vi.fn() })),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      removeSeries: vi.fn(),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      subscribeCrosshairMove: vi.fn((h: (p: unknown) => void) => {
        registry.crosshairHandlers.push(h);
      }),
      unsubscribeCrosshairMove: vi.fn(),
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
import {
  computeTooltipPosition,
  type TooltipPositionResult,
} from "@/components/charts/core/tooltipPosition";

const createChartMock = vi.mocked(lightweightCharts.createChart);
const createSeriesMarkersMock = vi.mocked(lightweightCharts.createSeriesMarkers);
const registry = (
  lightweightCharts as unknown as {
    __registry: {
      charts: any[];
      crosshairHandlers: Array<(p: unknown) => void>;
    };
  }
).__registry;
const posMock = vi.mocked(computeTooltipPosition);

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
    applyOptions: ReturnType<typeof vi.fn>;
    createPriceLine: ReturnType<typeof vi.fn>;
    markers?: unknown;
  }>;
}

beforeEach(() => {
  registry.charts.length = 0;
  registry.crosshairHandlers.length = 0;
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

  it("目标线并入 autoscale 值域（provider 包装默认实现，CARD-02）", () => {
    // 修复行为锚（a8e8134f）：库 autoScale 值域只按 series 数据算、priceLine
    // 不参与——线值越出数据域时整条被裁。承载 series（无 fitness → 目标线挂
    // bestScore）经 applyOptions 挂 autoscaleInfoProvider，把线值并入 min/max
    render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    const series = seriesPanel();
    expect(series[0].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ autoscaleInfoProvider: expect.any(Function) }),
    );
    const provider = series[0].applyOptions.mock.calls
      .map((c) => c[0] as { autoscaleInfoProvider?: (orig: () => unknown) => unknown })
      .find((o) => o?.autoscaleInfoProvider)?.autoscaleInfoProvider as (
      orig: () => unknown,
    ) => { priceRange?: { minValue: number; maxValue: number } };
    // 越域场景（进化初期得分 50-55，目标 60）：maxValue 并入线值
    const expanded = provider(() => ({ priceRange: { minValue: 50, maxValue: 55 } })) as {
      priceRange: { minValue: number; maxValue: number };
    };
    expect(expanded.priceRange).toEqual({ minValue: 50, maxValue: 60 });
    // original 无值域时原样透传 null，不造域
    expect(provider(() => null)).toBeNull();
  });

  it("达标线并入 autoscale 值域（fitness 形态 provider 挂 bestFitness series，CARD-02）", () => {
    // 线值=props targetFitness（0.5）：provider 闭包捕获的是 render 时的
    // 实际 props 值，非默认值——构造线值低于数据域场景验 minValue 方向
    render(<ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={0.5} />);
    const series = seriesPanel();
    // 承载 series 是 bestFitness（series[1]），非承载的 bestScore 不挂 provider
    expect(series[1].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ autoscaleInfoProvider: expect.any(Function) }),
    );
    expect(series[0].applyOptions).not.toHaveBeenCalledWith(
      expect.objectContaining({ autoscaleInfoProvider: expect.any(Function) }),
    );
    const provider = series[1].applyOptions.mock.calls
      .map((c) => c[0] as { autoscaleInfoProvider?: (orig: () => unknown) => unknown })
      .find((o) => o?.autoscaleInfoProvider)?.autoscaleInfoProvider as (
      orig: () => unknown,
    ) => { priceRange?: { minValue: number; maxValue: number } };
    // 线值低于数据域场景（fitness 数据域 0.6-1.1，达标线 0.5）：minValue 并入
    const expanded = provider(() => ({ priceRange: { minValue: 0.6, maxValue: 1.1 } })) as {
      priceRange: { minValue: number; maxValue: number };
    };
    expect(expanded.priceRange).toEqual({ minValue: 0.5, maxValue: 1.1 });
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

  it("filters non-finite generation records（脏 time 整点剔除，四路同步，CARD-04）", () => {
    // 修复行为锚（beff1d7a）：generation 脏值（NaN/null）dev 构建经库断言
    // throw 崩组件树、production 静默进数据模型——与 value 面同滤；脏点剔除
    // 后余点四路同步（gen1/gen4 存留）
    const dirty = [
      makeRecord({ generation: 1, best_score: 50, best_fitness: 0.6 }),
      makeRecord({ generation: Number.NaN, best_score: 70, best_fitness: 0.9 }),
      makeRecord({ generation: null as unknown as number, best_score: 52, best_fitness: 1.1 }),
      makeRecord({ generation: 4, best_score: 55, best_fitness: 1.0 }),
    ];
    render(<ScoreTrendChart records={dirty} targetScore={60} targetFitness={1.0} />);
    const series = seriesPanel();
    expect(series).toHaveLength(4);
    for (const s of series) {
      expect(s.setData).toHaveBeenCalledTimes(1);
      expect(s.setData.mock.calls[0][0]).toHaveLength(2);
    }
  });

  it("champion 脏 generation 补滤：markers 不含脏 time 冠军点（CARD-04）", () => {
    // markers 两数据源独立于 safeData 构造：transform 层按原始 records 判冠军
    //（gen2 score 70 > 50 入 championChanges），组件层 championMarkers 滤掉
    // 脏 generation——markers 只含合法冠军 gen1
    const dirtyChampion = [
      makeRecord({ generation: 1, best_score: 50 }),
      makeRecord({ generation: Number.NaN, best_score: 70 }),
    ];
    render(<ScoreTrendChart records={dirtyChampion} targetScore={60} />);
    expect(createSeriesMarkersMock).toHaveBeenCalledTimes(1);
    const markers = createSeriesMarkersMock.mock.calls[0][1] as Array<{ time: number }>;
    expect(markers.map((m) => Number(m.time))).toEqual([1]);
  });

  it("boundary 脏 generation 补滤：种群边界脏 time 不入 markers（CARD-04）", () => {
    // gen3 pop 1→2 触发 isPopulationBoundary（transform 层按原始 records 判定），
    // 组件层 boundaryMarkers 滤掉脏 generation——markers 只含两合法冠军点
    const dirtyBoundary = [
      makeRecord({ generation: 1, best_score: 50, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, best_score: 58, top3_summary: "pop=1" }),
      makeRecord({ generation: Number.NaN, best_score: 52, top3_summary: "pop=2" }),
    ];
    render(<ScoreTrendChart records={dirtyBoundary} targetScore={60} />);
    expect(createSeriesMarkersMock).toHaveBeenCalledTimes(1);
    const markers = createSeriesMarkersMock.mock.calls[0][1] as Array<{
      time: number;
      shape: string;
    }>;
    expect(markers.map((m) => Number(m.time))).toEqual([1, 2]);
    expect(markers.every((m) => m.shape === "circle")).toBe(true);
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
    // S-11 降级面：订阅挂不上，浮层不渲染
    expect(screen.queryByTestId("score-tooltip")).not.toBeInTheDocument();
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

// ---------------------------------------------------------------------------
// 悬停浮层链（fix-chart-tooltip-restore 组 3，stub getContext）：fixture 经
// transformChartData 真实变换（变换本身不重测，独立 L1 在 evolutionChart.test），
// 悬停注入走 crosshairHandlers 事件形制。嵌套行（综合得分+delta、适应度+达标）
// 用 toHaveTextContent 断整行+getByText 命中嵌套 span（文本节点命中本身承载
// React 文本渲染，R-D）
// ---------------------------------------------------------------------------

/** 全字段 fixture（5 代 pop=2 全程）：gen5 变换产物=delta+1.5/达标/累计 62/
 *  停滞 3 代/非冠军非边界（十字段中互斥行由 S-4/S-5 独立承载——champion 点
 *  cumulativeBest 恒等于 bestScore，累计行与冠军行不可同点在场，数据结构固有） */
const S1_RECORDS = [
  makeRecord({ generation: 1, best_score: 55.0, avg_score: 50.0, best_fitness: 0.95, top3_summary: 'pop=2|diag={"diversity":0.7,"avg_trades":3.0}' }),
  makeRecord({ generation: 2, best_score: 62.0, avg_score: 52.0, best_fitness: 1.0, top3_summary: "pop=2" }),
  makeRecord({ generation: 3, best_score: 59.7, avg_score: 54.0, best_fitness: 1.05, top3_summary: "pop=2" }),
  makeRecord({ generation: 4, best_score: 59.7, avg_score: 54.5, best_fitness: 1.05, top3_summary: "pop=2" }),
  makeRecord({ generation: 5, best_score: 61.2, avg_score: 55.0, best_fitness: 1.05, top3_summary: 'pop=2|diag={"diversity":0.75,"avg_trades":3.2}' }),
];

/** 悬停注入 helper：render 后以 gen 代注入 crosshair 事件（Score 侧 hover 反查
 *  只消费 time/point，seriesData 形制保持与 Equity 侧一致） */
function renderAndHover(
  records: EvolutionHistoryRecord[],
  gen: number,
  point = { x: 100, y: 50 },
) {
  const result = render(
    <ScoreTrendChart records={records} targetScore={60} targetFitness={1.0} />,
  );
  act(() => {
    // 取最后注册的 handler：同 it 内多次 render 时 [0] 是已卸载组件残留
    //（unmount 走 chart.remove()，mock 面不清注册表数组）
    registry.crosshairHandlers[registry.crosshairHandlers.length - 1]({
      time: gen,
      point,
      seriesData: new Map(),
    });
  });
  return result;
}

describe("ScoreTrendChart 悬停浮层链（S-1~S-12）", () => {
  let ctxSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    ctxSpy = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as CanvasRenderingContext2D);
  });
  afterEach(() => ctxSpy.mockRestore());

  it("S-1 十字段全字段在场逐字段文案（字段面缩水即不通过）", () => {
    renderAndHover(S1_RECORDS, 5);
    const tip = screen.getByTestId("score-tooltip");
    expect(screen.getByText("第 5 代")).toBeInTheDocument();
    expect(screen.getByText("P2")).toBeInTheDocument();
    // 嵌套行：整行 toHaveTextContent+附注 span getByText
    expect(tip).toHaveTextContent("综合得分: 61.2");
    expect(screen.getByText("+1.5")).toBeInTheDocument();
    expect(tip).toHaveTextContent("适应度: 1.05");
    expect(screen.getByText("达标")).toBeInTheDocument();
    // 独立行逐字
    expect(screen.getByText("累计最佳: 62.0")).toBeInTheDocument();
    expect(screen.getByText("平均得分: 55.0")).toBeInTheDocument();
    expect(screen.getByText("种群平均交易: 3.2")).toBeInTheDocument();
    expect(screen.getByText("种群多样性: 75%")).toBeInTheDocument();
    expect(screen.getByText("停滞: 3 代未突破")).toBeInTheDocument();
  });

  it("S-2 颜色档：diversity>0.7 行色/delta 正色/达标附注在场（JSX style 断言）", () => {
    renderAndHover(S1_RECORDS, 5);
    expect(screen.getByText("种群多样性: 75%")).toHaveStyle({ color: "#34d399" });
    expect(screen.getByText("+1.5")).toHaveStyle({ color: "#34d399" });
    expect(screen.getByText("达标")).toBeInTheDocument();
  });

  it("S-3 条件不在场参数化：六类条件行全不在场", () => {
    // 单代裸 fixture：population 缺省 1/bestFitness/avgTrades/diversity 全缺/
    // cumulativeBest===bestScore（首代冠军）/stagnationCount=0
    renderAndHover([makeRecord({ generation: 1, best_score: 50 })], 1);
    expect(screen.queryByText(/^P\d+$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/适应度:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/累计最佳:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/种群平均交易:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/种群多样性:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/停滞:/)).not.toBeInTheDocument();
    // 恒显行在场（对照组，防误断空浮层）
    expect(screen.getByText("第 1 代")).toBeInTheDocument();
    expect(screen.getByText("综合得分: 50.0")).toBeInTheDocument();
  });

  it("S-4 互斥+边界优先：双真点显示新种群开始、最优记录刷新不在场", () => {
    // gen2 pop 1→2 触发 boundary，58>50 同时是冠军——双真点
    const records = [
      makeRecord({ generation: 1, best_score: 50, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, best_score: 58, top3_summary: "pop=2" }),
    ];
    renderAndHover(records, 2);
    expect(screen.getByText("新种群开始")).toBeInTheDocument();
    expect(screen.queryByText("最优记录刷新")).not.toBeInTheDocument();
  });

  it("S-5 非 boundary 冠军点：最优记录刷新在场", () => {
    // gen1 首代冠军非边界（Pass2 从 i=1 起，首代永非 boundary）
    const records = [
      makeRecord({ generation: 1, best_score: 50, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, best_score: 45, top3_summary: "pop=1" }),
    ];
    renderAndHover(records, 1);
    expect(screen.getByText("最优记录刷新")).toBeInTheDocument();
    expect(screen.queryByText("新种群开始")).not.toBeInTheDocument();
  });

  it("S-6 delta 边界三态：boundary 无附注/delta===0 无附注/delta<0 负形在场", () => {
    // boundary 代：prevBestScore 置 undefined（Pass4），无任何附注
    const boundary = [
      makeRecord({ generation: 1, best_score: 50, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, best_score: 58, top3_summary: "pop=2" }),
    ];
    const { unmount: u1 } = renderAndHover(boundary, 2);
    expect(screen.queryByText("+8.0")).not.toBeInTheDocument();
    u1();
    // delta===0：同分连代，无 +0.0 附注
    const zero = [
      makeRecord({ generation: 1, best_score: 50 }),
      makeRecord({ generation: 2, best_score: 50 }),
    ];
    const { unmount: u2 } = renderAndHover(zero, 2);
    expect(screen.getByText("综合得分: 50.0")).toBeInTheDocument();
    expect(screen.queryByText("+0.0")).not.toBeInTheDocument();
    u2();
    // delta<0：负形 -0.8 附注在场
    const neg = [
      makeRecord({ generation: 1, best_score: 50 }),
      makeRecord({ generation: 2, best_score: 49.2 }),
    ];
    renderAndHover(neg, 2);
    expect(screen.getByText("-0.8")).toBeInTheDocument();
  });

  it("S-7 达标判定反面：bestFitness<targetFitness 无达标附注、适应度行仍在场", () => {
    renderAndHover(
      [makeRecord({ generation: 1, best_score: 50, best_fitness: 0.8 })],
      1,
    );
    const tip = screen.getByTestId("score-tooltip");
    expect(tip).toHaveTextContent("适应度: 0.80");
    expect(screen.queryByText("达标")).not.toBeInTheDocument();
  });

  it("S-8 缺失值非占位：optional 全缺时无 null/undefined 占位文本", () => {
    renderAndHover([makeRecord({ generation: 1, best_score: 50 })], 1);
    // 反向断言：缺失=行不在场，不是占位文本
    expect(screen.queryByText(/null|undefined/)).not.toBeInTheDocument();
  });

  it("S-9 反查同源：注入 time=2 显示第 2 代真实字段（两代数值可判别）", () => {
    renderAndHover(noFitnessRecords, 2);
    expect(screen.getByText("第 2 代")).toBeInTheDocument();
    expect(screen.getByText("综合得分: 55.0")).toBeInTheDocument();
    expect(screen.queryByText("第 1 代")).not.toBeInTheDocument();
  });

  it("S-10 离图双通道+状态转移", () => {
    render(<ScoreTrendChart records={noFitnessRecords} targetScore={60} />);
    // 通道一：time undefined 初态不在场
    act(() => {
      registry.crosshairHandlers[0]({
        time: undefined,
        point: undefined,
        seriesData: new Map(),
      });
    });
    expect(screen.queryByTestId("score-tooltip")).not.toBeInTheDocument();
    // 通道二：先悬停在场再 point undefined → 浮层消失（状态转移非仅初态）
    act(() => {
      registry.crosshairHandlers[0]({
        time: 2,
        point: { x: 100, y: 50 },
        seriesData: new Map(),
      });
    });
    expect(screen.getByTestId("score-tooltip")).toBeInTheDocument();
    act(() => {
      registry.crosshairHandlers[0]({
        time: 2,
        point: undefined,
        seriesData: new Map(),
      });
    });
    expect(screen.queryByTestId("score-tooltip")).not.toBeInTheDocument();
  });

  it("S-11 订阅唯一性：初始化订阅 1 次，records 重传后不重复", () => {
    const { rerender } = render(
      <ScoreTrendChart records={fitnessRecords} targetScore={60} targetFitness={1.0} />,
    );
    expect(registry.charts[0].subscribeCrosshairMove).toHaveBeenCalledTimes(1);
    rerender(
      <ScoreTrendChart records={[...fitnessRecords]} targetScore={60} targetFitness={1.0} />,
    );
    expect(registry.charts[0].subscribeCrosshairMove).toHaveBeenCalledTimes(1);
    expect(registry.crosshairHandlers).toHaveLength(1);
  });

  it("S-12 定位接线：受控返回用于 style+锚点=param.point（R-F 组件层承载）", () => {
    // jsdom measure 恒 0 使宽高传反类不可判别——该缺陷类归 e2e 真布局终审；
    // 本用例锁「返回值用于 style」与「锚点来源」两面
    posMock.mockClear();
    posMock.mockReturnValue({ left: 11, top: 22, placement: "top" } as TooltipPositionResult);
    renderAndHover(S1_RECORDS, 5);
    const tip = screen.getByTestId("score-tooltip");
    expect(tip.style.left).toBe("11px");
    expect(tip.style.top).toBe("22px");
    expect(posMock.mock.calls[0][0].anchor).toEqual({ x: 100, y: 50 });
  });
});
