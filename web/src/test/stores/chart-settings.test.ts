/**
 * Tests for stores/chart-settings.ts: Zustand store for chart indicator settings.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useChartSettings } from "@/stores/chart-settings";

describe("useChartSettings", () => {
  beforeEach(() => {
    // 每个用例前重置为缺省值
    useChartSettings.getState().resetToDefaults();
  });

  it("has default EMA list", () => {
    const { emaList } = useChartSettings.getState();
    expect(emaList.length).toBeGreaterThanOrEqual(3);
    expect(emaList[0].period).toBe(10);
  });

  it("addEma appends new entry", () => {
    useChartSettings.getState().addEma(100);
    const { emaList } = useChartSettings.getState();
    const last = emaList[emaList.length - 1];
    expect(last.period).toBe(100);
    expect(last.enabled).toBe(true);
  });

  it("removeEma removes by index", () => {
    const before = useChartSettings.getState().emaList.length;
    useChartSettings.getState().removeEma(0);
    expect(useChartSettings.getState().emaList.length).toBe(before - 1);
  });

  it("updateEma modifies specific entry", () => {
    useChartSettings.getState().updateEma(0, { period: 200 });
    expect(useChartSettings.getState().emaList[0].period).toBe(200);
  });

  it("reorderEma moves entry", () => {
    useChartSettings.getState().updateEma(0, { period: 10 });
    useChartSettings.getState().updateEma(1, { period: 20 });
    useChartSettings.getState().reorderEma(0, 1);
    const { emaList } = useChartSettings.getState();
    expect(emaList[0].period).toBe(20);
    expect(emaList[1].period).toBe(10);
  });

  it("setBoll updates boll config", () => {
    useChartSettings.getState().setBoll({ period: 30 });
    expect(useChartSettings.getState().boll.period).toBe(30);
  });

  it("setRsi updates rsi config", () => {
    useChartSettings.getState().setRsi({ overbought: 80 });
    expect(useChartSettings.getState().rsi.overbought).toBe(80);
  });

  it("setVol updates vol config", () => {
    useChartSettings.getState().setVol({ position: "separate" });
    expect(useChartSettings.getState().vol.position).toBe("separate");
  });

  it("resetToDefaults restores initial state", () => {
    useChartSettings.getState().addEma(999);
    useChartSettings.getState().setBoll({ period: 999 });
    useChartSettings.getState().resetToDefaults();
    expect(useChartSettings.getState().boll.period).not.toBe(999);
    // EMA 清单应回到缺省值
    const lastEma = useChartSettings.getState().emaList[useChartSettings.getState().emaList.length - 1];
    expect(lastEma.period).not.toBe(999);
  });

  it("getEmaPeriods returns enabled periods only", () => {
    useChartSettings.getState().updateEma(0, { enabled: false });
    const periods = useChartSettings.getState().getEmaPeriods();
    const all = useChartSettings.getState().emaList;
    expect(periods.length).toBeLessThan(all.length);
  });

  it("getBollParams returns period and std", () => {
    const params = useChartSettings.getState().getBollParams();
    expect(params).toHaveProperty("period");
    expect(params).toHaveProperty("std");
  });

  it("getIndicatorParams returns full config", () => {
    const params = useChartSettings.getState().getIndicatorParams();
    expect(params).toHaveProperty("ema_periods");
    expect(params).toHaveProperty("boll");
    expect(params).toHaveProperty("rsi");
    expect(params).toHaveProperty("vol");
  });
});
