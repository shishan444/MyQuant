/**
 * KlineChart 六面行为锚（kline-common-layer 组 1）——基线测试，改造前版本全绿=基线成立。
 * 断言走库 API 数据面（series setData/markers 数组）+DOM 读数节点，非像素断言（design §7：
 * jsdom 无真实绘制；像素面归 e2e）。lightweight-charts 以 fake registry 承载调用序列。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

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
  const mkChart = () => {
    const chart: any = {
      series: [] as any[],
      timeScale: vi.fn(() => mkTimeScale()),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      removeSeries: vi.fn((s: unknown) => {
        chart.series = chart.series.filter((x: unknown) => x !== s);
      }),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      addSeries: vi.fn((def: { _type?: string }, _opts?: unknown) => {
        const s: any = { seriesType: def?._type ?? "?", markers: null, setData: vi.fn(), applyOptions: vi.fn() };
        chart.series.push(s);
        return s;
      }),
      addPane: vi.fn(),
      panes: vi.fn(() => []),
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
    // 主题等间接消费的枚举面（chartThemes.ts）
    ColorType: { Solid: "solid", VerticalGradient: "verticalGradient" },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
    CrosshairMode: { Normal: 0, Magnet: 1, Hidden: 2 },
    createSeriesMarkers: vi.fn((series: { markers: unknown }, markers: unknown[]) => {
      series.markers = markers;
      return { setMarkers: vi.fn((m: unknown[]) => { series.markers = m; }) };
    }),
  };
});

import { KlineChart } from "@/components/charts/KlineChart";
import { toTime } from "@/components/charts/core/time";
import { useChartSettings } from "@/stores/chart-settings";
import * as lightweightCharts from "lightweight-charts";

const registry = (lightweightCharts as unknown as { __registry: { charts: any[]; crosshairHandlers: any[] } }).__registry;

const CANDLES = [
  { timestamp: "2026-01-01 00:00:00", open: 100, high: 110, low: 95, close: 105 },
  { timestamp: "2026-01-01 04:00:00", open: 105, high: 115, low: 100, close: 112 },
  { timestamp: "2026-01-01 08:00:00", open: 112, high: 120, low: 108, close: 118 },
];
const BOLL = {
  upper: [{ time: "2026-01-01 00:00:00", value: 120 }, { time: "2026-01-01 04:00:00", value: 125 }],
  middle: [{ time: "2026-01-01 00:00:00", value: 105 }, { time: "2026-01-01 04:00:00", value: 108 }],
  lower: [{ time: "2026-01-01 00:00:00", value: 90 }, { time: "2026-01-01 04:00:00", value: 92 }],
};
const VOLUME = [
  { time: "2026-01-01 00:00:00", value: 100, color: "#26a69a" },
  { time: "2026-01-01 04:00:00", value: 150, color: "#ef5350" },
];
const SIGNALS = [
  { type: "open_long", side: "long", timestamp: "2026-01-01 00:00:00" },
  { type: "close_long", side: "long", timestamp: "2026-01-01 08:00:00" },
] as const;
const TRIGGERS = [
  { time: "2026-01-01 04:00:00", direction: "up", label: "▲" },
] as const;

function setup() {
  registry.charts.length = 0;
  registry.crosshairHandlers.length = 0;
  const utils = render(
    <KlineChart
      data={CANDLES as never}
      bollData={BOLL as never}
      volumeData={VOLUME as never}
      signals={SIGNALS as never}
      triggers={TRIGGERS as never}
      height={400}
    />,
  );
  return { ...utils, reg: registry };
}

function candleSeriesOf(reg: { charts: any[] }) {
  const main = reg.charts[0];
  return main.series.find((s: { seriesType: string }) => s.seriesType === "Candlestick");
}

describe("KlineChart 六面行为锚（基线）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("面① 蜡烛：Candlestick series setData=排序后的 OHLC 映射", () => {
    const { reg } = setup();
    const cs = candleSeriesOf(reg);
    expect(cs).toBeTruthy();
    const data = cs.setData.mock.calls[0][0] as Array<{ time: number; open: number }>;
    expect(data).toHaveLength(3);
    expect(data[0]).toMatchObject({ time: toTime("2026-01-01 00:00:00") as number, open: 100, close: 105 });
    expect(data.map((d) => d.time)).toEqual([...data.map((d) => d.time)].sort((a, b) => a - b));
  });

  it("面② BOLL 三线：三条 Line series 各自 setData 逐点", () => {
    const { reg } = setup();
    const lines = reg.charts[0].series.filter((s: { seriesType: string }) => s.seriesType === "Line");
    // BOLL upper/middle/lower 三线（可能另有指标线；以 setData 值集识别三线）
    const dataSets = lines.map((s: { setData: { mock: { calls: unknown[][] } } }) => s.setData.mock.calls[0]?.[0]);
    const flat = JSON.stringify(dataSets);
    expect(flat).toContain("120"); // upper[0]
    expect(flat).toContain("105"); // middle[0]
    expect(flat).toContain("90"); // lower[0]
  });

  it("面③ 量能：Histogram series setData 携带颜色分区", () => {
    const { reg } = setup();
    const hists = reg.charts[0].series.filter((s: { seriesType: string }) => s.seriesType === "Histogram");
    const volSet = hists.find((h: { setData: { mock: { calls: unknown[][] } } }) =>
      JSON.stringify(h.setData.mock.calls[0]?.[0] ?? []).includes("#ef5350"),
    );
    expect(volSet).toBeTruthy();
  });

  it("面④ 信号与触发点：B/S→markers 映射（open_long→OL belowBar）+▲触发点 offset series", () => {
    const { reg } = setup();
    const cs = candleSeriesOf(reg);
    const markers = cs.markers as Array<{ text: string; position: string; time: number }>;
    expect(markers).toHaveLength(2);
    expect(markers[0]).toMatchObject({ text: "OL", position: "belowBar", time: toTime("2026-01-01 00:00:00") as number });
    expect(markers[1]).toMatchObject({ text: "CL", position: "aboveBar" });
    // ▲触发点走独立 offset series 的 markers
    const offsetLine = reg.charts[0].series.find(
      (s: { seriesType: string; markers: unknown }) => s.seriesType === "Line" && s.markers,
    );
    expect(offsetLine).toBeTruthy();
  });

  it("面⑤ 十字光标：crosshair 回调写 legend 读数（DOM 文本）", () => {
    const { reg } = setup();
    const cs = candleSeriesOf(reg);
    act(() => {
      reg.crosshairHandlers[0]({
        time: 1767225600,
        seriesData: new Map([[cs, { close: 123.456 }]]),
      });
    });
    expect(screen.getByText(/C: 123\.46/)).toBeInTheDocument();
  });

  it("面⑥ 设置持久化：store 变更写 localStorage(chart-indicator-settings)", () => {
    setup();
    act(() => {
      useChartSettings.getState().addEma(30);
    });
    const raw = localStorage.getItem("chart-indicator-settings");
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string).state.emaList).toEqual(
      expect.arrayContaining([expect.objectContaining({ period: 30 })]),
    );
  });
});

describe("KlineChart 纯函数层（core/time toTime）", () => {
  it("ISO 与空格分隔形制都转 UTCTimestamp 秒", () => {
    expect(toTime("2026-01-01T00:00:00")).toBe(toTime("2026-01-01 00:00:00"));
    expect(typeof toTime("2026-01-01 00:00:00")).toBe("number");
  });
  it("malformed 输入回退 date-only 切片不抛错", () => {
    expect(toTime("bad-date-xx")).toBe("bad-date-xx".slice(0, 10));
  });
});
