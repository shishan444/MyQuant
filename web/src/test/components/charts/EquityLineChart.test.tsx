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
    // priceScale 固定返回同一 api 实例：组件内多次调用 priceScale() 后
    // 断言面才能命中被调用的那个 applyOptions（每次新建则断言必落空）
    const priceScaleApi = { applyOptions: vi.fn() };
    const chart: any = {
      series: [] as any[],
      timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
      applyOptions: vi.fn(),
      remove: vi.fn(),
      priceScale: vi.fn(() => priceScaleApi),
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
    AreaSeries: { _type: "Area" },
    // chartThemes 顶层 import 的枚举（DARK_CHART_THEME 装配面）——组件经
    // withAlpha 引入 chartThemes 传递依赖，mock 面须覆盖其模块加载需求
    ColorType: { Solid: "solid" },
    LineStyle: { Solid: "solid", Dotted: "dotted", Dashed: "dashed" },
    CrosshairMode: { Normal: "normal" },
  };
});

import * as lightweightCharts from "lightweight-charts";
import { EquityLineChart, resolveEquityColor, EQUITY_MIN_POINTS } from "@/components/charts/EquityLineChart";
import { withAlpha } from "@/components/charts/core/chartThemes";
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
  it("语义 A 空数组边角：last ?? baseline fallback 至 baseline（恒按盈利计）", () => {
    // 现状记录：data 空时 data[length-1] 为 undefined，?? 落到 baseline，
    // baseline >= baseline 恒真 → up（导出函数可直调到达；组件层该形态走空态早退）
    expect(resolveEquityColor([], 100000)).toBe(POLARITY.up);
  });

  it("着色不变量：结果只依赖末点与 baseline，中间路径不改变（属性面）", () => {
    // 对任意有限中间序列保持的关系：同末点同 baseline 恒同色——升/降/
    // 锯齿三条中间路径互异，末点一致则色一致（着色语义=终点盈亏判定）
    const mk = (vals: number[]) =>
      vals.map((v, i) => ({ timestamp: `2026-04-01T0${i}:00:00Z`, value: v }));
    const paths = [mk([99000, 99500, 101000]), mk([103000, 102000, 101000]), mk([99000, 103000, 99000, 101000])];
    const colors = new Set(paths.map((p) => resolveEquityColor(p, 100000)));
    expect(colors.size).toBe(1);
    expect([...colors][0]).toBe(POLARITY.up);
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

  it("creates 1 chart / 1 area series，data 全量进 setData（零抽样）", () => {
    render(<EquityLineChart data={pts(5, 101500)} baseline={100000} />);
    expect(createChartMock).toHaveBeenCalledTimes(1);
    const chart = registry.charts[0];
    expect(chart.series).toHaveLength(1);
    expect(chart.series[0].seriesType).toBe("Area");
    expect(chart.series[0].setData).toHaveBeenCalledTimes(1);
    expect(chart.series[0].setData.mock.calls[0][0]).toHaveLength(5);
  });

  it("fades area gradient from 0.3 alpha to transparent（lineColor 三态随迁 AreaSeries）", () => {
    // 旧 EquityCurve 渐变锚承接（CARD-03）：AreaSeries 线条色选项名 lineColor
    //（非 LineSeries 的 color）；topColor 0.3 透明度→bottomColor 全透明，
    // up/down/gold 三态各验
    const { unmount: u1 } = render(<EquityLineChart data={pts(5, 101500)} baseline={100000} />);
    expect(registry.charts[0].series[0].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({
        lineColor: POLARITY.up,
        topColor: withAlpha(POLARITY.up, 0.3),
        bottomColor: withAlpha(POLARITY.up, 0),
      }),
    );
    u1();
    const { unmount: u2 } = render(<EquityLineChart data={pts(5, 99000)} baseline={100000} />);
    expect(registry.charts[1].series[0].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({
        lineColor: POLARITY.down,
        topColor: withAlpha(POLARITY.down, 0.3),
        bottomColor: withAlpha(POLARITY.down, 0),
      }),
    );
    u2();
    render(<EquityLineChart data={pts(5, 101000)} />);
    expect(registry.charts[2].series[0].applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({
        lineColor: ATTENTION.gold,
        topColor: withAlpha(ATTENTION.gold, 0.3),
        bottomColor: withAlpha(ATTENTION.gold, 0),
      }),
    );
  });

  it("applies y 轴 padding 等价形（scaleMargins 0.1）+ autoSize 自适应（现状记录）", () => {
    // design D1 授权等价形：旧 y 轴 ±500 padding → priceScale 百分比 margin；
    // ResizeObserver 自适应 → lightweight autoSize 内建容器监听
    render(<EquityLineChart data={pts(5, 101500)} baseline={100000} />);
    expect(createChartMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ autoSize: true }),
    );
    const chart = registry.charts[0];
    expect(chart.priceScale).toHaveBeenCalledWith("right");
    expect(chart.priceScale().applyOptions).toHaveBeenCalledWith({
      autoScale: true,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    });
  });

  it("timestamps 转 epoch 秒进 setData（UTCTimestamp 承接形制，值面记录）", () => {
    const data = pts(3, 100300);
    render(<EquityLineChart data={data} />);
    const points = registry.charts[0].series[0].setData.mock.calls[0][0] as Array<{
      time: number;
      value: number;
    }>;
    expect(points[0].time).toBe(Date.parse(data[0].timestamp) / 1000);
    expect(points[2].time).toBe(Date.parse(data[2].timestamp) / 1000);
    expect(points[0].value).toBe(100000);
    expect(points[2].value).toBe(100300);
  });

  it("filters NaN-timestamp points before setData（非法串剔除不崩）", () => {
    const poisoned = [...pts(3, 100300), { timestamp: "not-a-date", value: 1 }];
    render(<EquityLineChart data={poisoned} />);
    const series = registry.charts[0].series[0];
    expect(series.setData).toHaveBeenCalledTimes(1);
    expect(series.setData.mock.calls[0][0]).toHaveLength(3);
  });

  it("filters non-finite value points before setData（NaN/null 两形制剔除不崩）", () => {
    // CARD-03（security S-1）：value 面有限性兜底——dev 构建 checkLineItem 对
    // NaN throw（typeof 过首条、isSafeValue 恒 false 次条拦）、null/字符串首条
    // 拦；production 断言剥除后静默脏数据。NaN 与 null 同为 Number.isFinite 拦截面
    const poisoned = [
      ...pts(3, 100300),
      { timestamp: "2026-04-01T09:00:00Z", value: Number.NaN },
      { timestamp: "2026-04-01T10:00:00Z", value: null as unknown as number },
    ];
    render(<EquityLineChart data={poisoned} />);
    const series = registry.charts[0].series[0];
    expect(series.setData).toHaveBeenCalledTimes(1);
    expect(series.setData.mock.calls[0][0]).toHaveLength(3);
  });

  it("末点脏 value 剔除后按有效末点着色（着色与绘制同源，CARD-05）", () => {
    // 修复行为锚（d70f64ba）：原始末点 value 脏（被 validData 剔除）时，着色
    // 不得取原始末点——NaN >= baseline 恒 false 会误显亏损色；着色取过滤后
    // 序列末点（101200 > baseline → up），与实际绘制末点同源
    const poisoned = [
      { timestamp: "2026-04-01T00:00:00Z", value: 101000 },
      { timestamp: "2026-04-01T01:00:00Z", value: 101200 },
      { timestamp: "2026-04-01T02:00:00Z", value: Number.NaN },
    ];
    render(<EquityLineChart data={poisoned} baseline={100000} />);
    const series = registry.charts[0].series[0];
    expect(series.setData.mock.calls[0][0]).toHaveLength(2);
    expect(series.applyOptions).toHaveBeenCalledWith(
      expect.objectContaining({ lineColor: POLARITY.up }),
    );
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
