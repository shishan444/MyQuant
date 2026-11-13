/**
 * ChartHud 四浮层组件测（fe-asset-data-domain 2.3——内容级真值锚）。
 * 覆盖：标题浮层（symbol·tf·最新价）/仓位浮层（方向·数量·开仓价·浮盈亏
 * 正负着色）/信号浮层（kind+outcome；空态「暂无信号记录」）/成交浮层
 * （kind price×qty；空态「暂无成交」）——va 域取色锚。
 * t3r5 补测（G4）：浮盈亏正/负取色断言落地（原用例名声称未断言——虚标
 * 修正）+截断契约（信号至多 3/成交至多 2）+lastPrice=null 缺失值「—」。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChartHud } from "@/components/trading/ChartHud";
import { CHART_VA, POLARITY } from "@/theme/tokens";
import type { PaperFill, PaperSignal } from "@/types/api";

// hex → jsdom CSSOM 序列化 rgb 元组（fe-polarity-calibration T2.5：断言值
// 与源同源——期望从 tokens 派生而非 rgb 字面第二实现，调值自动跟随）
const rgbTuple = (hex: string): string =>
  [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(", ");

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

  it("仓位浮层：做多·数量·开仓价·浮盈亏（正=amber 取色）", () => {
    ui();
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("做多");
    expect(hud).toHaveTextContent("数量 0.010000");
    expect(hud).toHaveTextContent("开仓 94000.00");
    expect(hud).toHaveTextContent("浮盈亏 120.50");
    // 正浮盈亏取色=CHART_VA.amber（#FFB020——jsdom rgb 序列化）。
    // 谓词带 style 过滤：外层开仓行 span 的 textContent 也含「浮盈亏」但无 style
    const upnlSpan = [...hud.querySelectorAll("span")].find((s) =>
      s.textContent?.includes("浮盈亏") && s.hasAttribute("style"));
    expect(upnlSpan?.getAttribute("style")).toContain("rgb(255, 176, 32)");
  });

  it("浮盈亏负值：呈现 -50.00+取色 POLARITY.down（着色三元 False 侧）", () => {
    ui({ position: { side: "short", quantity: 0.02, entryPrice: 95000, upnl: -50 } });
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("做空");
    expect(hud).toHaveTextContent("浮盈亏 -50.00");
    // 负浮盈亏取色=POLARITY.down（jsdom rgb 序列化；期望=rgbTuple(POLARITY.down)
    // 引用形——fe-polarity-calibration T2.5「断言值与源同源」，rgb 字面形退役）
    const upnlSpan = [...hud.querySelectorAll("span")].find((s) =>
      s.textContent?.includes("浮盈亏") && s.hasAttribute("style"));
    expect(upnlSpan?.getAttribute("style")).toContain(`rgb(${rgbTuple(POLARITY.down)})`);
  });

  it("信号/成交浮层：kind+outcome 与成交回执真值", () => {
    ui();
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("开仓 · filled");
    expect(hud).toHaveTextContent("开仓 95000.00 × 0.010000");
  });

  it("空态如实：空仓/无信号/无成交", () => {
    ui({ position: { side: null, quantity: null, entryPrice: null, upnl: null }, signals: [], fills: [] });
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("空仓");
    expect(hud).toHaveTextContent("暂无信号记录");
    expect(hud).toHaveTextContent("暂无成交");
    void CHART_VA;
  });

  it("截断契约：信号至多 3/成交至多 2；lastPrice=null 呈「—」", () => {
    // 传入数组按组件契约已是倒序（最新在前——reverse 归上游 TradingChart/Trading）
    const sig = (seq: number, kind: string): PaperSignal =>
      ({ ...signal, seq, signal_kind: kind }) as PaperSignal;
    const fl = (id: number, price: number): PaperFill =>
      ({ ...fill, id, price }) as PaperFill;
    ui({
      lastPrice: null,
      signals: [sig(1, "open"), sig(2, "close"), sig(3, "add"), sig(4, "reduce")],
      fills: [fl(1, 91000), fl(2, 92000), fl(3, 93000)],
    });
    const hud = screen.getByTestId("chart-hud");
    expect(hud).toHaveTextContent("最新 —");
    // 信号 slice(0,3)：seq 1-3 在场（title 锚），第 4 条被截
    expect(hud.querySelector('[title*="seq=1"]')).not.toBeNull();
    expect(hud.querySelector('[title*="seq=3"]')).not.toBeNull();
    expect(hud.querySelector('[title*="seq=4"]')).toBeNull();
    // 成交 slice(0,2)：前 2 笔在场，第 3 笔被截
    expect(hud).toHaveTextContent("91000.00");
    expect(hud).toHaveTextContent("92000.00");
    expect(hud).not.toHaveTextContent("93000.00");
  });
});
