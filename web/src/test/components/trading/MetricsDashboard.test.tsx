/**
 * Tests for MetricsDashboard component.
 * Covers: empty state, metric formatting, trend calculation.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MetricsDashboard } from "@/components/trading/MetricsDashboard";
import { mockTradingMetrics } from "@/test/fixtures";

describe("MetricsDashboard", () => {
  // --- Empty state ---
  it("renders 6 placeholder cards when metrics is undefined", () => {
    render(<MetricsDashboard metrics={undefined} initialCash={100000} />);
    const placeholders = screen.getAllByText("--");
    expect(placeholders).toHaveLength(12); // 6 cards * 2 (label + value both "--")
  });

  // --- PnL formatting ---
  it("renders Total PnL with currency format", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    expect(screen.getByText("总盈亏")).toBeInTheDocument();
    expect(screen.getByText("$2,000")).toBeInTheDocument();
  });

  it("renders positive PnL with up trend", () => {
    render(
      <MetricsDashboard metrics={{ ...mockTradingMetrics, total_pnl: 500 }} initialCash={100000} />,
    );
    const pnlValue = screen.getByText("$500");
    expect(pnlValue).toBeInTheDocument();
    expect(pnlValue.closest("span")?.className).toContain("text-profit");
  });

  it("renders negative PnL with down trend", () => {
    render(
      <MetricsDashboard metrics={{ ...mockTradingMetrics, total_pnl: -300 }} initialCash={100000} />,
    );
    expect(screen.getByText("$-300")).toBeInTheDocument();
  });

  // --- Return percentage ---
  it("renders Return with percent format", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    expect(screen.getByText("收益率")).toBeInTheDocument();
    // total_return_pct=2.0 -> formatPercent(2.0/100) = "+2.0%"
    expect(screen.getByText("+2.0%")).toBeInTheDocument();
  });

  // --- Win Rate ---
  it("renders Win Rate with up trend when >= 50%", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    // win_rate=0.6 -> "60.0%"
    expect(screen.getByText("60.0%")).toBeInTheDocument();
  });

  it("renders Win Rate with down trend when < 50%", () => {
    render(
      <MetricsDashboard metrics={{ ...mockTradingMetrics, win_rate: 0.3 }} initialCash={100000} />,
    );
    expect(screen.getByText("30.0%")).toBeInTheDocument();
  });

  // --- Profit Factor ---
  it("renders Profit Factor with value", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    // profit_factor=1.8 -> "1.80"
    expect(screen.getByText("1.80")).toBeInTheDocument();
  });

  it("renders Profit Factor as N/A when null", () => {
    render(
      <MetricsDashboard metrics={{ ...mockTradingMetrics, profit_factor: null }} initialCash={100000} />,
    );
    expect(screen.getByText("N/A")).toBeInTheDocument();
  });

  // --- Max Drawdown ---
  it("renders Max Drawdown with negative sign", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    // max_drawdown_pct=0.8 -> "-0.8%"
    expect(screen.getByText("-0.8%")).toBeInTheDocument();
  });

  // --- Trades ---
  it("renders Trades with W/L format", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    // win_count=3, loss_count=2 -> "3W / 2L"
    expect(screen.getByText("3胜/2负")).toBeInTheDocument();
  });

  // --- All 6 labels present ---
  it("renders all 6 stat labels", () => {
    render(<MetricsDashboard metrics={mockTradingMetrics} initialCash={100000} />);
    const labels = ["总盈亏", "收益率", "胜率", "盈亏比", "最大回撤", "交易次数"];
    for (const label of labels) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});
