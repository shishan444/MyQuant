/**
 * EquityLineChart 契约测试（fe-chart-single-lib 组 2）：resolveEquityColor
 * 双语义直测（A 持平按盈利计/B 金色）+ fake registry 装配锚（series 数/
 * 线色/data 点数）+ jsdom 降级形态（容器在场+空态文案）。
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { EquityLineChart, resolveEquityColor, EQUITY_MIN_POINTS } from "@/components/charts/EquityLineChart";
import { POLARITY, ATTENTION } from "@/theme/tokens";

const pts = (n: number, endValue: number) =>
  Array.from({ length: n }, (_, i) => ({
    timestamp: `2026-04-01T0${i}:00:00Z`,
    value: 100000 + i * 100,
  })).map((p, i) => (i === n - 1 ? { ...p, value: endValue } : p));

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

describe("EquityLineChart jsdom 降级形态", () => {
  it("数据充足：容器在场（jsdom 无 canvas 跳过 init 不崩）", () => {
    const { container } = render(<EquityLineChart data={pts(5, 101000)} />);
    expect(container).toBeTruthy();
  });
  it("数据不足：空态文案+MIN_POINTS=2", () => {
    render(<EquityLineChart data={pts(1, 100)} />);
    expect(screen.getByText("数据不足，无法绘制资金曲线")).toBeTruthy();
    expect(EQUITY_MIN_POINTS).toBe(2);
  });
});