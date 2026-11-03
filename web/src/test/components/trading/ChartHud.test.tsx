/**
 * ChartHud 四浮层组件测（fe-asset-data-domain 2.3——内容级真值锚）。
 * 覆盖：标题浮层（symbol·tf·最新价）/仓位浮层（方向·数量·开仓价·浮盈亏
 * 正负着色）/信号浮层（kind+outcome；空态「暂无信号记录」）/成交浮层
 * （kind price×qty；空态「暂无成交」）——va 域取色锚。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChartHud } from "@/components/trading/ChartHud";
import { CHART_VA } from "@/theme/tokens";
import type { PaperFill, PaperSignal } from "@/types/api";

const fill = {
  id: 1, task_id: "t1", bar_time: "2026-11-01 00:00:00+00:00", seq: 0,
  kind: "open", side: "buy", price: 95000, quantity: 0.01, fee: 1,
  realized_pnl: null, reason: "gene_hit",
} as PaperFill;

const signal = {
  id: 1, task_id: "t1", strategy_id: "s1",
  bar_time: "2026-11-01 00:00:00+00:00", seq: 0, signal_kind: "open",
  direction: "long", size_fraction: null, gene_hits: [], indicator_snapshot: {},
  outcome: "filled", reject_reason: null, fill_seq: 1, capped: null,
  equity: 100000, balance: 100000, unrealized_pnl: 0,
  position_side: "long", position_quantity: 0.01, position_margin: 50,
  created_at: "2026-11-01T00:00:00Z",
} as PaperSignal;

function ui(over: Partial<Parameters<typeof ChartHud>[0]> = {}) {
  return render(
    <ChartHud
      symbol="BTCUSDT"
      timeframe="1h"
      lastPrice={95000}
      position={{ side: "long", quantity: 0.01, entryPrice: 94000, upnl: 120.5 }}
      signals={[signal]}
      fills={[fill]}
      {...over}
    />,
  );
}

describe("ChartHud 四浮层（va 变体真值锚）", () => {
  it("标题浮层：symbol·tf·最新价（amber 强调取色）", () => {
    const { container } = ui();
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("BTCUSDT · 1H");
    expect(hud).toHaveTextContent("最新 95000.00");
    // va 底取色（jsdom rgb 序列化）
    // #050505CC → rgba(5,5,5,0.8)（jsdom 序列化带 alpha）
    expect(hud.getAttribute("style")).toContain("rgba(5, 5, 5, 0.8)");
    void container;
  });

  it("仓位浮层：做多·数量·开仓价·浮盈亏（正=amber）", () => {
    ui();
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("做多");
    expect(hud).toHaveTextContent("数量 0.010000");
    expect(hud).toHaveTextContent("开仓 94000.00");
    expect(hud).toHaveTextContent("浮盈亏 120.50");
  });

  it("信号/成交浮层：kind+outcome 与成交回执真值", () => {
    ui();
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("开仓 · filled");
    expect(hud).toHaveTextContent("开仓 95000.00 × 0.010000");
  });

  it("空态如实：空仓/无信号/无成交；浮盈亏负=POLARITY.down", () => {
    ui({ position: { side: null, quantity: null, entryPrice: null, upnl: null }, signals: [], fills: [] });
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("空仓");
    expect(hud).toHaveTextContent("暂无信号记录");
    expect(hud).toHaveTextContent("暂无成交");
    void CHART_VA;
  });
});
