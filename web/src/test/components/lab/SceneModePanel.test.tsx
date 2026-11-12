/**
 * SceneModePanel 装配冒烟（CARD-02 补归——P-4 stretch factor 组件级改动的
 * Lab 场景模式消费方回归）：选中场景类型后 KlineChart 真挂载，主副 pane
 * stretch 0.75/0.25 比例参数在场景模式图同样生效、渲染零异常。lightweight-
 * charts 以 fake registry 承载调用序列（与 KlineChart.test.tsx 同款形制）。
 * 「副图挤压」为视觉面，jsdom 无绘制——比例参数锚+挂载零异常为单测等价
 * 面，像素面归 e2e/截图矩阵（形制如实标注）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("lightweight-charts", () => {
  const registry = {
    charts: [] as any[],
    crosshairHandlers: [] as Array<(p: unknown) => void>,
  };
  const mkTimeScale = () => ({
    setVisibleRange: vi.fn(),
    getVisibleLogicalRange: () => null,
    setVisibleLogicalRange: vi.fn(),
    fitContent: vi.fn(),
    subscribeVisibleLogicalRangeChange: vi.fn(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(),
    applyOptions: vi.fn(),
  });
  const mkPane = () => ({ setStretchFactor: vi.fn(), setHeight: vi.fn() });
  const mkChart = () => {
    const panes = [mkPane(), mkPane()];
    const chart: any = {
      series: [] as any[],
      timeScale: vi.fn(() => mkTimeScale()),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      removeSeries: vi.fn((s: unknown) => {
        chart.series = chart.series.filter((x: unknown) => x !== s);
      }),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      addSeries: vi.fn((def: { _type?: string }) => {
        const s: any = { seriesType: def?._type ?? "?", markers: null, setData: vi.fn(), applyOptions: vi.fn() };
        chart.series.push(s);
        return s;
      }),
      addPane: vi.fn(() => {
        panes.push(mkPane());
        return panes[panes.length - 1];
      }),
      panes: vi.fn(() => panes),
      subscribeCrosshairMove: vi.fn((h: (p: unknown) => void) => registry.crosshairHandlers.push(h)),
    };
    registry.charts.push(chart);
    return chart;
  };
  return {
    __registry: registry,
    createChart: vi.fn(mkChart),
    CandlestickSeries: { _type: "Candlestick" },
    LineSeries: { _type: "Line" },
    HistogramSeries: { _type: "Histogram" },
    ColorType: { Solid: "solid", VerticalGradient: "verticalGradient" },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
    CrosshairMode: { Normal: 0, Magnet: 1, Hidden: 2 },
    createSeriesMarkers: vi.fn((series: { markers: unknown }, markers: unknown[]) => {
      series.markers = markers;
      return { setMarkers: vi.fn((m: unknown[]) => { series.markers = m; }) };
    }),
  };
});

vi.mock("@/hooks/useChartIndicators", () => ({
  useChartIndicators: () => ({
    candleData: [
      { timestamp: "2026-01-01 00:00:00", open: 100, high: 110, low: 95, close: 105 },
      { timestamp: "2026-01-01 04:00:00", open: 105, high: 115, low: 100, close: 112 },
      { timestamp: "2026-01-01 08:00:00", open: 112, high: 120, low: 108, close: 118 },
    ],
    chartIndicators: [],
    chartBollData: null,
    volumeData: [{ time: "2026-01-01 00:00:00", value: 100, color: "#26a69a" }],
    macdData: null,
    kdjData: null,
  }),
}));

vi.mock("@/services/scene", () => ({
  getSceneTypes: vi.fn().mockResolvedValue({
    types: [{ id: "double_top", label: "双顶", description: "双顶形态" }],
  }),
  verifyScene: vi.fn(),
}));

vi.mock("@/hooks/useDatasets", () => ({
  useAvailableSources: () => ({
    queryKey: ["test-available-sources"],
    queryFn: () => Promise.resolve({ sources: [] }),
    staleTime: Infinity,
  }),
}));

import * as lightweightCharts from "lightweight-charts";
import { SceneModePanel } from "@/components/lab/SceneModePanel";

const registry = (lightweightCharts as unknown as { __registry: { charts: any[]; crosshairHandlers: any[] } }).__registry;

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SceneModePanel />
    </QueryClientProvider>,
  );
}

describe("SceneModePanel 装配冒烟（CARD-02 补归——P-4 消费方回归）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    registry.charts.length = 0;
    registry.crosshairHandlers.length = 0;
  });

  it("选中场景类型后 KlineChart 挂载：单 chart+panes 化+主副 stretch 0.75/0.25 生效（未选零挂载）", async () => {
    renderPanel();
    // 未选场景类型（hasSelection=false）：图表区不渲染
    expect(registry.charts).toHaveLength(0);
    fireEvent.click(await screen.findByText("双顶"));
    // 选中后 KlineChart 真挂载（candleData 经 hook mock 恒非空）
    await waitFor(() => {
      expect(registry.charts).toHaveLength(1);
    });
    const main = registry.charts[0];
    expect(main.addPane).toHaveBeenCalled();
    // P-4 主副 3:1 比例参数在场景模式图同样生效（组件级共享实现）
    const panes = main.panes();
    expect(panes[0].setStretchFactor).toHaveBeenCalledWith(0.75);
    expect(panes[panes.length - 1].setStretchFactor).toHaveBeenCalledWith(0.25);
    // 蜡烛通量：数据到位（渲染链路完整走通）
    const cs = main.series.find((s: { seriesType: string }) => s.seriesType === "Candlestick");
    expect(cs).toBeTruthy();
    expect(cs.setData.mock.calls[0][0]).toHaveLength(3);
  });
});
