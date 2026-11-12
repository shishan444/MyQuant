/**
 * 标注原语纯函数层单测（kline-common-layer 组 4.2）。
 */
import { describe, it, expect, vi } from "vitest";
import type { IChartApi, IPriceLine, ISeriesApi, Time } from "lightweight-charts";
import { POLARITY } from "@/theme/tokens";
import {
  fillsToSignals,
  signalsToMarkers,
  triggersToMarkers,
  fillAction,
  toEntryLineOptions,
  toStopLossLineOptions,
  toBandData,
  SIGNAL_STYLE,
  PositionBand,
  attachPositionBand,
  attachEntryLine,
  attachStopLossLine,
} from "@/components/charts/primitives";
import { toTime } from "@/components/charts/core/time";
import {
  SegmentBand,
  attachSegmentBand,
  toSegmentBands,
} from "@/components/charts/primitives/segmentBand";

describe("markers 纯函数层", () => {
  it("fillsToSignals：出场 side=null/缺省 不画、五 exit kind 归并 close", () => {
    const signals = fillsToSignals([
      { kind: "open", side: "long", bar_time: "2026-01-01 00:00:00" },
      { kind: "exit_sl", side: "long", bar_time: "2026-01-01 04:00:00" },
      { kind: "exit_max_hold", side: null, bar_time: "2026-01-01 08:00:00" },
      // side 缺省（PaperFill.side? 可选三态——CARD-04 收宽后与 null 同不画）
      { kind: "exit_time", bar_time: "2026-01-01 12:00:00" },
    ]);
    expect(signals).toEqual([
      { type: "open_long", timestamp: "2026-01-01 00:00:00" },
      { type: "close_long", timestamp: "2026-01-01 04:00:00" },
    ]);
  });

  it("fillAction 白名单四值外归并：exit_tp→close、open→open", () => {
    expect(fillAction("exit_tp")).toBe("close");
    expect(fillAction("exit_liquidation")).toBe("close");
    expect(fillAction("open")).toBe("open");
    expect(fillAction("add")).toBe("add");
  });

  it("signalsToMarkers：样式表映射+未知类型回落 buy", () => {
    const markers = signalsToMarkers(
      [
        { type: "open_long", timestamp: "2026-01-01 00:00:00" },
        { type: "reduce_short", timestamp: "2026-01-01 04:00:00" },
        { type: "mystery", timestamp: "2026-01-01 08:00:00" },
      ],
      toTime,
    );
    expect(markers[0]).toMatchObject({ text: "OL", position: "belowBar" });
    expect(markers[1]).toMatchObject({ text: "RS", position: "belowBar" });
    expect(markers[2]).toMatchObject({ text: "B" });
    expect(markers[0].time).toBe(toTime("2026-01-01 00:00:00"));
  });

  it("triggersToMarkers：▲ 方向决定上下位与颜色", () => {
    const markers = triggersToMarkers(
      [
        { time: "2026-01-01 00:00:00", direction: "up" },
        { time: "2026-01-01 04:00:00", direction: "down" },
      ],
      toTime,
    );
    expect(markers[0]).toMatchObject({ text: "▲", position: "belowBar" });
    expect(markers[1]).toMatchObject({ position: "aboveBar" });
  });

  it("SIGNAL_STYLE 十值全覆盖（白名单四操作×双向+旧词 buy/sell）", () => {
    const keys = Object.keys(SIGNAL_STYLE);
    expect(keys).toHaveLength(10);
    for (const action of ["open", "close", "add", "reduce"]) {
      for (const side of ["long", "short"]) {
        expect(keys).toContain(`${action}_${side}`);
      }
    }
  });

  it("SIGNAL_STYLE 十值逐值锚（CARD-04 双写收敛同值替换——期望=收敛前 KlineChart 内联表历史基线）", () => {
    expect(SIGNAL_STYLE).toEqual({
      buy: { position: "belowBar", color: POLARITY.up, shape: "arrowUp", text: "B" },
      sell: { position: "aboveBar", color: POLARITY.down, shape: "arrowDown", text: "S" },
      open_long: { position: "belowBar", color: POLARITY.up, shape: "arrowUp", text: "OL" },
      close_long: { position: "aboveBar", color: "#FF9800", shape: "arrowDown", text: "CL" },
      add_long: { position: "belowBar", color: POLARITY.up, shape: "circle", text: "AL" },
      reduce_long: { position: "aboveBar", color: "#FF9800", shape: "circle", text: "RL" },
      open_short: { position: "aboveBar", color: POLARITY.down, shape: "arrowDown", text: "OS" },
      close_short: { position: "belowBar", color: "#7C4DFF", shape: "arrowUp", text: "CS" },
      add_short: { position: "aboveBar", color: POLARITY.down, shape: "square", text: "AS" },
      reduce_short: { position: "belowBar", color: "#7C4DFF", shape: "square", text: "RS" },
    });
  });
});

describe("价线族纯函数层", () => {
  it("入场线 options：金实线+轴标签", () => {
    const opts = toEntryLineOptions({ price: 61240.5, label: "ENTRY" });
    expect(opts).toMatchObject({ price: 61240.5, lineStyle: 0, title: "ENTRY", axisLabelVisible: true });
  });

  it("止损线 options：trail 虚线黄+fixed 实线红（07-B4-2 两档语义）", () => {
    expect(toStopLossLineOptions({ price: 60000, kind: "trail", visible: true })).toMatchObject({
      lineStyle: 2,
      color: "#FBBF24",
      title: "SL(trail)",
    });
    expect(toStopLossLineOptions({ price: 59000, kind: "fixed", visible: true })).toMatchObject({
      lineStyle: 0,
      color: POLARITY.down,
      title: "SL",
    });
  });
});

describe("持仓带纯函数层", () => {
  it("toBandData：时间键经 toTime 转数字 UTCTimestamp（与图表数据键同形制，CARD-02 后非字符串透传）；持仓中 exitTime 缺省", () => {
    const entry = toBandData({ bar_time: "2026-01-01 00:00:00", side: "long" });
    // 期望经 toTime 计算（无时区标记串按本地时区解析，硬编码秒值在异时区机器必红）；
    // toTime 为独立已锚依赖函数，非被测对象——行为对照锚形制
    expect(entry).toEqual({
      entryTime: toTime("2026-01-01 00:00:00"),
      exitTime: undefined,
      side: "long",
    });
    // 数字键锚：锁「字符串透传→toTime 转换」修复面（回退即 typeof "string" 红）
    expect(typeof entry.entryTime).toBe("number");

    const closed = toBandData(
      { bar_time: "2026-01-01 00:00:00", side: "short" },
      { bar_time: "2026-01-02 00:00:00" },
    );
    expect(closed.entryTime).toBe(toTime("2026-01-01 00:00:00"));
    expect(closed.exitTime).toBe(toTime("2026-01-02 00:00:00"));
    expect(closed.side).toBe("short");
  });
});

// ---------------------------------------------------------------------------
// 挂载面（attachPrimitive/createPriceLine 通道）——系列与图表为结构化 mock，
// 断言原语生命周期与 draw 坐标换算，不依赖真实图表实例。
// ---------------------------------------------------------------------------

function mkSeries() {
  return {
    attachPrimitive: vi.fn(),
    createPriceLine: vi.fn(() => ({}) as IPriceLine),
  } as unknown as ISeriesApi<"Candlestick", Time>;
}

function mkBandHarness(coords: Record<string, number | null>) {
  const timeToCoordinate = vi.fn((t: string) => (t in coords ? coords[t] : null));
  const timeScale = { timeToCoordinate };
  const chart = { timeScale: vi.fn(() => timeScale) } as unknown as IChartApi;
  const requestUpdate = vi.fn();
  const band = new PositionBand();
  band.attached({ chart, requestUpdate });
  return { band, chart, timeScale, requestUpdate };
}

function drawCapture(band: PositionBand) {
  const fillRect = vi.fn();
  const scope = {
    context: { fillRect },
    mediaSize: { width: 600, height: 300 },
  };
  for (const view of band.paneViews()) {
    view.renderer()?.draw({ useMediaCoordinateSpace: (cb: (s: typeof scope) => void) => cb(scope) } as never);
  }
  return fillRect;
}

describe("PositionBand 挂载与生命周期", () => {
  it("attachPositionBand：attachPrimitive 调用一次且参数为 PositionBand 实例，返回 setData 通道", () => {
    const series = mkSeries();
    const { primitive, setData } = attachPositionBand(series);
    expect(series.attachPrimitive).toHaveBeenCalledTimes(1);
    expect(series.attachPrimitive).toHaveBeenCalledWith(primitive);
    expect(primitive).toBeInstanceOf(PositionBand);
    // setData 通道可在 attached 前调用不炸（requestUpdate 未就绪为可选链）
    expect(() => setData({ entryTime: "t0" as Time, side: "long" })).not.toThrow();
  });

  it("attached 后 setData 触发 requestUpdate 重绘请求", () => {
    const { band, requestUpdate } = mkBandHarness({});
    band.setData({ entryTime: "t0" as Time, side: "long" });
    expect(requestUpdate).toHaveBeenCalledTimes(1);
  });

  it("draw 坐标直画：timeToCoordinate 的 media 坐标零换算、exitTime 缺省延伸至右缘（CARD-01 后 DPI 无关）", () => {
    const { band } = mkBandHarness({ entry: 10.5 });
    band.setData({ entryTime: "entry" as never, side: "long" });
    band.updateAllViews();
    const fillRect = drawCapture(band);
    expect(fillRect).toHaveBeenCalledTimes(1);
    // x1=10.5 原值直画；x2 缺省=mediaSize.width=600 → 宽 589.5；全高 300
    expect(fillRect).toHaveBeenCalledWith(10.5, 0, 589.5, 300);
  });

  it("draw 颜色分区：long=绿系半透明、short=红系半透明", () => {
    for (const [side, rgba] of [
      ["long", "rgba(45,212,191,0.08)"],
      ["short", "rgba(251,113,133,0.08)"],
    ] as const) {
      const { band } = mkBandHarness({ entry: 10 });
      band.setData({ entryTime: "entry" as never, side });
      band.updateAllViews();
      const fillRect = vi.fn();
      const ctx = { fillRect, fillStyle: "" };
      const scope = { context: ctx, mediaSize: { width: 600, height: 300 } };
      for (const view of band.paneViews()) {
        view.renderer()?.draw({ useMediaCoordinateSpace: (cb: (s: typeof scope) => void) => cb(scope) } as never);
      }
      expect(ctx.fillStyle).toBe(rgba);
    }
  });

  it("entry 坐标 null（时间不在可见域）：renderer 拿空数据不绘制", () => {
    const { band } = mkBandHarness({ entry: null });
    band.setData({ entryTime: "entry" as never, side: "long" });
    band.updateAllViews();
    expect(drawCapture(band)).not.toHaveBeenCalled();
  });

  it("exitTime 有坐标：带区间宽度=x2-x1（media 坐标差）", () => {
    const { band } = mkBandHarness({ entry: 10.5, exit: 100.25 });
    band.setData({ entryTime: "entry" as never, exitTime: "exit" as never, side: "short" });
    band.updateAllViews();
    const fillRect = drawCapture(band);
    expect(fillRect).toHaveBeenCalledWith(10.5, 0, 89.75, 300);
  });

  it("detached 后 updateAllViews 不再触碰图表（chart 置空）", () => {
    const { band, chart } = mkBandHarness({});
    band.setData({ entryTime: "t0" as Time, side: "long" });
    band.detached();
    band.updateAllViews();
    expect(chart.timeScale).not.toHaveBeenCalled();
  });
});

describe("价线族挂载通道", () => {
  it("attachEntryLine：createPriceLine 透传 options（price/title）", () => {
    const series = mkSeries();
    attachEntryLine(series, { price: 61240.5, label: "ENTRY" });
    expect(series.createPriceLine).toHaveBeenCalledTimes(1);
    const opts = (series.createPriceLine as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(opts).toMatchObject({ price: 61240.5, title: "ENTRY", axisLabelVisible: true });
  });

  it("attachStopLossLine：visible=false 不挂载返回 null；visible=true 挂载", () => {
    const series = mkSeries();
    expect(attachStopLossLine(series, { price: 60000, kind: "trail", visible: false })).toBeNull();
    expect(series.createPriceLine).not.toHaveBeenCalled();
    expect(attachStopLossLine(series, { price: 60000, kind: "trail", visible: true })).toBeTruthy();
    expect(series.createPriceLine).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 段界竖带原语（upgrade-vr-fullrange-chart 契约 B）——多带单控制器、
// 双色交替、段名文字、超域跳过、全清（jsdom 下 canvas 调用采集断言）
// ---------------------------------------------------------------------------
function mkSegHarness(coords: Record<string, number | null>) {
  const timeToCoordinate = vi.fn((t: string) => (t in coords ? coords[t] : null));
  const timeScale = { timeToCoordinate };
  const chart = { timeScale: vi.fn(() => timeScale) } as unknown as IChartApi;
  const requestUpdate = vi.fn();
  const band = new SegmentBand();
  band.attached({ chart, requestUpdate });
  return { band, requestUpdate };
}

describe("SegmentBand 段界竖带（契约 B）", () => {
  it("toSegmentBands：执行份映射段带数据（时间经 toTime 数字形、label=exec_ref）", () => {
    const bands = toSegmentBands([
      { exec_ref: "趋势", range_start: "2026-01-01 00:00:00", range_end: "2026-01-02 00:00:00" },
    ]);
    expect(bands).toEqual([{
      startTime: toTime("2026-01-01 00:00:00"),
      endTime: toTime("2026-01-02 00:00:00"),
      label: "趋势",
    }]);
  });

  it("attachSegmentBand：attachPrimitive 一次+返回 setData 通道", () => {
    const series = mkSeries();
    const { primitive, setData } = attachSegmentBand(series);
    expect(series.attachPrimitive).toHaveBeenCalledWith(primitive);
    expect(() => setData([{ startTime: 1 as never, endTime: 2 as never, label: "x" }])).not.toThrow();
  });

  it("多带渲染：双色交替+段名 fillText+全量替换+全清", () => {
    const { band } = mkSegHarness({ t1: 10, t2: 100, t3: 200, t4: 300 });
    band.setData([
      { startTime: "t1" as never, endTime: "t2" as never, label: "趋势" },
      { startTime: "t3" as never, endTime: "t4" as never, label: "震荡" },
    ]);
    band.updateAllViews();
    const fillRect = vi.fn();
    const fillText = vi.fn();
    const styles: string[] = [];
    const ctx = {
      fillRect,
      fillText,
      set fillStyle(v: string) { styles.push(v); },
      get fillStyle() { return styles[styles.length - 1] ?? ""; },
      font: "",
      textBaseline: "",
      save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), rect: vi.fn(), clip: vi.fn(),
    };
    const scope = { context: ctx, mediaSize: { width: 600, height: 300 } };
    for (const view of band.paneViews()) {
      view.renderer()?.draw({ useMediaCoordinateSpace: (cb: (s: typeof scope) => void) => cb(scope) } as never);
    }
    // 两带各一次 fillRect；双色交替（8% → 14%）
    expect(fillRect).toHaveBeenCalledTimes(2);
    expect(fillRect).toHaveBeenNthCalledWith(1, 10, 0, 90, 300);
    expect(fillRect).toHaveBeenNthCalledWith(2, 200, 0, 100, 300);
    expect(styles).toContain("rgba(59,130,246,0.08)");
    expect(styles).toContain("rgba(59,130,246,0.14)");
    // 段名文字带内顶部（x1+4、10px、TEXT.primary 色）
    expect(fillText).toHaveBeenCalledTimes(2);
    expect(fillText).toHaveBeenNthCalledWith(1, "趋势", 14, 4);
    expect(styles).toContain("#F4F2FF");
    // 全量替换：setData 新集替换旧集（一换一）
    band.setData([{ startTime: "t1" as never, endTime: "t2" as never, label: "新段" }]);
    band.updateAllViews();
    const fillRect2 = vi.fn();
    const ctx2 = { ...ctx, fillRect: fillRect2, fillText: vi.fn() };
    for (const view of band.paneViews()) {
      view.renderer()?.draw({ useMediaCoordinateSpace: (cb: (s: typeof scope) => void) => cb({ context: ctx2, mediaSize: scope.mediaSize }) } as never);
    }
    expect(fillRect2).toHaveBeenCalledTimes(1);
    // 全清：null → 零渲染
    band.setData(null);
    band.updateAllViews();
    const fillRect3 = vi.fn();
    const ctx3 = { ...ctx, fillRect: fillRect3, fillText: vi.fn() };
    for (const view of band.paneViews()) {
      view.renderer()?.draw({ useMediaCoordinateSpace: (cb: (s: typeof scope) => void) => cb({ context: ctx3, mediaSize: scope.mediaSize }) } as never);
    }
    expect(fillRect3).not.toHaveBeenCalled();
  });

  it("超域跳过：任一端坐标转换 null（超可视域）该带不渲染", () => {
    const { band } = mkSegHarness({ t1: 10 }); // t2 无坐标
    band.setData([{ startTime: "t1" as never, endTime: "t2" as never, label: "x" }]);
    band.updateAllViews();
    const fillRect = vi.fn();
    const ctx = { fillRect, fillText: vi.fn(), save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), rect: vi.fn(), clip: vi.fn() };
    const scope = { context: ctx, mediaSize: { width: 600, height: 300 } };
    for (const view of band.paneViews()) {
      view.renderer()?.draw({ useMediaCoordinateSpace: (cb: (s: typeof scope) => void) => cb(scope) } as never);
    }
    expect(fillRect).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 观测序列断线顺修（upgrade-vr-fullrange-chart 契约 F）——无效点产
// whitespace 真断线（修正前 filter 剔除后剩余点连线）
// ---------------------------------------------------------------------------
import { attachObservationSeries, type ObservationPoint } from "@/components/charts/primitives/observationSeries";

describe("attachObservationSeries whitespace 断线（契约 F）", () => {
  function mkObsChart() {
    const series: any[] = [];
    const chart = {
      addSeries: vi.fn(() => {
        const s = { setData: vi.fn() };
        series.push(s);
        return s;
      }),
    } as never;
    return { chart, series };
  }
  it("无效点产 whitespace 项（不剔除——序列保留位次真断线）", () => {
    const { chart, series } = mkObsChart();
    const pts: ObservationPoint[] = [
      { time: "2026-01-01T00:00:00Z", value: 100 },
      { time: "2026-01-01T04:00:00Z", value: null },
      { time: "2026-01-01T08:00:00Z", value: 101 },
    ];
    attachObservationSeries(chart, pts);
    expect(series[0].setData).toHaveBeenCalledWith([
      { time: expect.any(Number), value: 100 },
      { time: expect.any(Number) },
      { time: expect.any(Number), value: 101 },
    ]);
  });
  it("全无效点返回 null（无有效点容错）", () => {
    const { chart, series } = mkObsChart();
    expect(
      attachObservationSeries(chart, [
        { time: "2026-01-01T00:00:00Z", value: null },
      ]),
    ).toBeNull();
    expect(series).toHaveLength(0);
  });
});
