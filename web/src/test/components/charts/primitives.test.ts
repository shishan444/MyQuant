/**
 * 标注原语纯函数层单测（kline-common-layer 组 4.2）。
 */
import { describe, it, expect } from "vitest";
import {
  fillsToSignals,
  signalsToMarkers,
  triggersToMarkers,
  fillAction,
  toEntryLineOptions,
  toStopLossLineOptions,
  toBandData,
  SIGNAL_STYLE,
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
