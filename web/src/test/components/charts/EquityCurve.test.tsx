/**
 * Tests for shared EquityCurve (echarts): L1 option 纯函数直测 + L2 组件生命周期（mock echarts/core）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("echarts/core", () => {
  const chart = { setOption: vi.fn(), dispose: vi.fn(), resize: vi.fn() };
  const init = vi.fn(() => chart);
  return { init, use: vi.fn(), __chart: chart };
});

import * as echartsCore from "echarts/core";
import { EquityCurve } from "@/components/charts/EquityCurve";
import {
  buildEquityOption,
  resolveEquityColor,
  EQUITY_MIN_POINTS,
} from "@/components/charts/core/equityOption";
import { CHART_PALETTE } from "@/components/charts/core/chartPalette";

const initMock = vi.mocked(echartsCore.init);
const chartMock = initMock() as unknown as { setOption: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> };

const profitData = [
  { timestamp: "2025-01-01T00:00:00Z", value: 100000 },
  { timestamp: "2025-01-10T00:00:00Z", value: 110000 },
];
const lossData = [
  { timestamp: "2025-01-01T00:00:00Z", value: 100000 },
  { timestamp: "2025-01-10T00:00:00Z", value: 90000 },
];

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// L1: resolveEquityColor
// ---------------------------------------------------------------------------

describe("resolveEquityColor", () => {
  it("returns gold when no baseline provided", () => {
    expect(resolveEquityColor(profitData)).toBe(CHART_PALETTE.gold);
  });

  it("returns profit color when final value above baseline", () => {
    expect(resolveEquityColor(profitData, 100000)).toBe(CHART_PALETTE.profit);
  });

  it("returns loss color when final value below baseline", () => {
    expect(resolveEquityColor(lossData, 100000)).toBe(CHART_PALETTE.loss);
  });

  it("treats break-even as profit color", () => {
    const flat = [
      { timestamp: "2025-01-01T00:00:00Z", value: 100000 },
      { timestamp: "2025-01-10T00:00:00Z", value: 100000 },
    ];
    expect(resolveEquityColor(flat, 100000)).toBe(CHART_PALETTE.profit);
  });
});

// ---------------------------------------------------------------------------
// L1: buildEquityOption
// ---------------------------------------------------------------------------

describe("buildEquityOption", () => {
  const option = buildEquityOption(profitData, 100000);
  // Arrayable 收窄为已知数组（组件恒返回单 series 数组）
  const seriesOf = (opt: ReturnType<typeof buildEquityOption>) =>
    opt.series as unknown as Array<{
      type: string;
      data: unknown;
      lineStyle: { color: string };
      areaStyle: { color: { colorStops: Array<{ offset: number; color: string }> } };
    }>;

  it("maps values into line series in order", () => {
    const series = seriesOf(option)[0];
    expect(series.type).toBe("line");
    expect(series.data).toEqual([100000, 110000]);
  });

  it("maps timestamps onto category axis", () => {
    const xAxis = option.xAxis as { type: string; data: string[] };
    expect(xAxis.type).toBe("category");
    expect(xAxis.data).toEqual(profitData.map((d) => d.timestamp));
  });

  it("colors line by profit/loss against baseline", () => {
    const profitSeries = seriesOf(buildEquityOption(profitData, 100000))[0];
    const lossSeries = seriesOf(buildEquityOption(lossData, 100000))[0];
    expect(profitSeries.lineStyle.color).toBe(CHART_PALETTE.profit);
    expect(lossSeries.lineStyle.color).toBe(CHART_PALETTE.loss);
  });

  it("fades area gradient from 0.3 alpha to transparent", () => {
    const series = seriesOf(option)[0] as {
      areaStyle: { color: { colorStops: Array<{ offset: number; color: string }> } };
    };
    const stops = series.areaStyle.color.colorStops;
    expect(stops).toHaveLength(2);
    expect(stops[0].color).toContain("rgba(0,200,83,0.3)");
    expect(stops[1].color).toContain("rgba(0,200,83,0)");
  });

  it("formats money labels with K/M abbreviation", () => {
    const yAxis = option.yAxis as {
      axisLabel: { formatter: (v: number) => string };
    };
    expect(yAxis.axisLabel.formatter(1_500_000)).toBe("$1.50M");
    expect(yAxis.axisLabel.formatter(1_500)).toBe("$1.5K");
    expect(yAxis.axisLabel.formatter(500)).toBe("$500");
  });

  it("formats time labels as MM-DD HH:mm", () => {
    const xAxis = option.xAxis as {
      axisLabel: { formatter: (v: string) => string };
    };
    expect(xAxis.axisLabel.formatter("2025-01-01T08:30:00Z")).toBe("01-01 08:30");
  });

  it("tooltip shows time and equity value for hovered point", () => {
    const tooltip = option.tooltip as unknown as {
      formatter: (params: unknown) => string;
    };
    const html = tooltip.formatter({ dataIndex: 1 });
    expect(html).toContain("01-10 00:00");
    expect(html).toContain("Equity: $110.0K");
  });
});

// ---------------------------------------------------------------------------
// L2: component lifecycle (mock echarts/core)
// ---------------------------------------------------------------------------

describe("EquityCurve component", () => {
  it("shows empty state when fewer than 2 points", () => {
    render(<EquityCurve data={[{ timestamp: "2025-01-01T00:00:00Z", value: 1 }]} />);
    expect(screen.getByText("数据不足，无法绘制资金曲线")).toBeInTheDocument();
    expect(initMock).not.toHaveBeenCalled();
    expect(EQUITY_MIN_POINTS).toBe(2);
  });

  it("inits chart and sets option with data", () => {
    render(<EquityCurve data={profitData} baseline={100000} />);
    expect(initMock).toHaveBeenCalledTimes(1);
    expect(chartMock.setOption).toHaveBeenCalledTimes(1);
    const passed = chartMock.setOption.mock.calls[0][0];
    const series = (passed.series as unknown as Array<{ type: string; data: unknown }>)[0];
    expect(series.type).toBe("line");
    expect(series.data).toEqual([100000, 110000]);
  });

  it("re-sets option without merge when baseline changes", () => {
    const { rerender } = render(<EquityCurve data={profitData} baseline={100000} />);
    rerender(<EquityCurve data={lossData} baseline={100000} />);
    const lastCall = chartMock.setOption.mock.calls.at(-1)!;
    expect(lastCall[0].series[0].lineStyle.color).toBe(CHART_PALETTE.loss);
    expect(lastCall[1]).toEqual({ notMerge: true });
  });

  it("disposes chart on unmount", () => {
    const { unmount } = render(<EquityCurve data={profitData} />);
    unmount();
    expect(chartMock.dispose).toHaveBeenCalled();
  });
});
