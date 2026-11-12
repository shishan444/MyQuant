/**
 * EquityLineChart 契约测试（fe-chart-single-lib 组 2）：resolveEquityColor
 * 双语义直测（A 持平按盈利计/B 金色）+ fake registry 装配锚（createChart 1 次/
 * series 数/线色/data 点数）+ jsdom 降级形态（canDraw 判空跳过 init+容器在场+
 * 空态文案）。装配锚形制对齐 KlineChart.test（registry 承载调用序列）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("lightweight-charts", () => {
  const registry = { charts: [] as any[] };
  const mkChart = () => {
    const chart: any = {
      series: [] as any[],
      timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      addSeries: vi.fn((def: { _type?: string }, options: unknown) => {
        const s: any = {
          seriesType: def?._type ?? "?",
          options,
          setData: vi.fn(),
          applyOptions: vi.fn(),
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
    LineSeries: { _type: "Line" },
  };
});

import * as lightweightCharts from "lightweight-charts";
import { EquityLineChart, resolveEquityColor, EQUITY_MIN_POINTS } from "@/components/charts/EquityLineChart";
import { POLARITY, ATTENTION } from "@/theme/tokens";

const createChartMock = vi.mocked(lightweightCharts.createChart);
const registry = (lightweightCharts as unknown as { __registry: { charts: any[] } }).__registry;

const pts = (n: number, endValue: number) =>
  Array.from({ length: n }, (_, i) => ({
    timestamp: `2026-04-01T0${i}:00:00Z`,
    value: 100000 + i * 100,
  })).map((p, i) => (i === n - 1 ? { ...p, value: endValue } : p));

beforeEach(() => {
  registry.charts.length = 0;
  vi.clearAllMocks();
});

describe("resolveEquityColor 双语义分立", () => {
  it("语义 B：无 baseline 金色", () => {
    expect(resolveEquityColor(pts(5, 101000))).toBe(ATTENTION.gold);
  });
  it("语义 A：盈利/亏损/持平按盈利计（>= 边角）", () => {
    expect(resolveEquityColor(pts(5, 101500), 100000)).toBe(POLARITY.up);
    expect(resolveEquityColor(pts(5, 99000), 100000)).toBe(POLARITY.down);
    expect(resolveEquityColor(pts(5, 100000), 100000)).toBe(POLARITY.up);
  });
});

describe("EquityLineChart 装配（fake registry）", () => {
  // canDraw 为 getContext("2d") 调用判空（C-2 修复形制）——jsdom 原生返回 null
  // 会让装配路径不触发；装配锚须还原浏览器形制（2d 上下文非空）才走到 createChart
  let ctxSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    ctxSpy = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as CanvasRenderingContext2D);
  });
  afterEach(() => ctxSpy.mockRestore());

  it("creates 1 chart / 1 line series，data 全量进 setData（零抽样）", () => {
    render(<EquityLineChart data={pts(5, 101500)} baseline={100000} />);
    expect(createChartMock).toHaveBeenCalledTimes(1);
    const chart = registry.charts[0];
    expect(chart.series).toHaveLength(1);
    expect(chart.series[0].seriesType).toBe("Line");
    expect(chart.series[0].setData).toHaveBeenCalledTimes(1);
    expect(chart.series[0].setData.mock.calls[0][0]).toHaveLength(5);
  });

  it("applies resolveEquityColor output as series color（语义 A 盈利/B 金色）", () => {
    const { unmount: u1 } = render(<EquityLineChart data={pts(5, 101500)} baseline={100000} />);
    expect(registry.charts[0].series[0].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ color: POLARITY.up }),
    );
    u1();
    render(<EquityLineChart data={pts(5, 101000)} />);
    expect(registry.charts[1].series[0].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ color: ATTENTION.gold }),
    );
  });

  it("filters NaN-timestamp points before setData（非法串剔除不崩）", () => {
    const poisoned = [...pts(3, 100300), { timestamp: "not-a-date", value: 1 }];
    render(<EquityLineChart data={poisoned} />);
    const series = registry.charts[0].series[0];
    expect(series.setData).toHaveBeenCalledTimes(1);
    expect(series.setData.mock.calls[0][0]).toHaveLength(3);
  });
});

describe("EquityLineChart jsdom 降级形态", () => {
  // 此 describe 不 stub getContext——jsdom 原生 getContext("2d") 返回 null，
  // canDraw=false 即降级路径（C-2 修复的降级生效锚：跳过 init 不崩不初始化）
  it("数据充足：容器在场且降级跳过 init（createChart 不被调用）", () => {
    const { container } = render(<EquityLineChart data={pts(5, 101000)} />);
    expect(container).toBeTruthy();
    expect(createChartMock).not.toHaveBeenCalled();
  });
  it("数据不足：空态文案+MIN_POINTS=2", () => {
    render(<EquityLineChart data={pts(1, 100)} />);
    expect(screen.getByText("数据不足，无法绘制资金曲线")).toBeTruthy();
    expect(EQUITY_MIN_POINTS).toBe(2);
  });
});
