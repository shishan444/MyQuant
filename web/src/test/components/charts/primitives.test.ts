/**
 * 标注原语纯函数层单测（kline-common-layer 组 4.2）。
 */
import { describe, it, expect, vi } from "vitest";
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

describe("markers 纯函数层", () => {
  it("fillsToSignals：出场 side=null 不画、五 exit kind 归并 close", () => {
    const signals = fillsToSignals([
      { kind: "open", side: "long", bar_time: "2026-01-01 00:00:00" },
      { kind: "exit_sl", side: "long", bar_time: "2026-01-01 04:00:00" },
      { kind: "exit_max_hold", side: null, bar_time: "2026-01-01 08:00:00" },
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
      color: "#EF4444",
      title: "SL",
    });
  });
});

describe("持仓带纯函数层", () => {
  it("toBandData：方向与区间透传（持仓中 exitTime 缺省）", () => {
    expect(toBandData({ bar_time: "2026-01-01 00:00:00", side: "long" })).toEqual({
      entryTime: "2026-01-01 00:00:00",
      exitTime: undefined,
      side: "long",
    });
    expect(
      toBandData({ bar_time: "2026-01-01 00:00:00", side: "short" }, { bar_time: "2026-01-02 00:00:00" }),
    ).toMatchObject({ exitTime: "2026-01-02 00:00:00", side: "short" });
  });
});

// ---------------------------------------------------------------------------
// 挂载面（attachPrimitive/createPriceLine 通道）——系列与图表为结构化 mock，
// 断言原语生命周期与 draw 坐标换算，不依赖真实图表实例。
// ---------------------------------------------------------------------------

function mkSeries() {
  return { attachPrimitive: vi.fn(), createPriceLine: vi.fn(() => ({})) } as never;
}

function mkBandHarness(coords: Record<string, number | null>) {
  const timeToCoordinate = vi.fn((t: string) => (t in coords ? coords[t] : null));
  const timeScale = { timeToCoordinate };
  const chart = { timeScale: vi.fn(() => timeScale) } as never;
  const requestUpdate = vi.fn();
  const band = new PositionBand();
  band.attached({ chart, requestUpdate });
  return { band, chart, timeScale, requestUpdate };
}

function drawCapture(band: PositionBand) {
  const fillRect = vi.fn();
  const scope = {
    context: { fillRect },
    horizontalPixelRatio: 2,
    mediaSize: { width: 600, height: 300 },
  };
  for (const view of band.paneViews()) {
    view.renderer()?.draw({ useBitmapCoordinateSpace: (cb: (s: typeof scope) => void) => cb(scope) } as never);
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
    expect(() => setData({ entryTime: 100, side: "long" })).not.toThrow();
  });

  it("attached 后 setData 触发 requestUpdate 重绘请求", () => {
    const { band, requestUpdate } = mkBandHarness({});
    band.setData({ entryTime: 100, side: "long" });
    expect(requestUpdate).toHaveBeenCalledTimes(1);
  });

  it("draw 坐标换算：x 坐标×DPR、exitTime 缺省延伸至右缘、long 带绿色", () => {
    const { band } = mkBandHarness({ entry: 10.5 });
    band.setData({ entryTime: "entry" as never, side: "long" });
    band.updateAllViews();
    const fillRect = drawCapture(band);
    expect(fillRect).toHaveBeenCalledTimes(1);
    // x1=10.5×2=21；x2 缺省=mediaSize.width=600 → 宽 579；全高 300
    expect(fillRect).toHaveBeenCalledWith(21, 0, 579, 300);
  });

  it("draw 颜色分区：long=绿系半透明、short=红系半透明", () => {
    for (const [side, rgba] of [
      ["long", "rgba(0, 200, 83, 0.08)"],
      ["short", "rgba(239, 68, 68, 0.08)"],
    ] as const) {
      const { band } = mkBandHarness({ entry: 10 });
      band.setData({ entryTime: "entry" as never, side });
      band.updateAllViews();
      const fillRect = vi.fn();
      const ctx = { fillRect, fillStyle: "" };
      const scope = { context: ctx, horizontalPixelRatio: 1, mediaSize: { width: 600, height: 300 } };
      for (const view of band.paneViews()) {
        view.renderer()?.draw({ useBitmapCoordinateSpace: (cb: (s: typeof scope) => void) => cb(scope) } as never);
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

  it("exitTime 有坐标：带区间宽度=x2×DPR-x1×DPR", () => {
    const { band } = mkBandHarness({ entry: 10.5, exit: 100.25 });
    band.setData({ entryTime: "entry" as never, exitTime: "exit" as never, side: "short" });
    band.updateAllViews();
    const fillRect = drawCapture(band);
    expect(fillRect).toHaveBeenCalledWith(21, 0, 200.5 - 21, 300);
  });

  it("detached 后 updateAllViews 不再触碰图表（chart 置空）", () => {
    const { band, chart } = mkBandHarness({});
    band.setData({ entryTime: 100, side: "long" });
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
