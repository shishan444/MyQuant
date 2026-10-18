/**
 * Tests for MetricsDashboard component（报告第一层主指标——新 props report+task）.
 * Covers: empty state, metric formatting, trend calculation.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MetricsDashboard } from "@/components/trading/MetricsDashboard";
import { mockTradingReport, mockTradingTask } from "@/test/fixtures";

describe("MetricsDashboard", () => {
  // --- Empty state ---
  it("renders 6 placeholder cards when report is undefined", () => {
    render(<MetricsDashboard report={undefined} task={mockTradingTask} />);
    const placeholders = screen.getAllByText("--");
    expect(placeholders).toHaveLength(12); // 6 cards * 2 (label + value both "--")
  });

  // --- PnL formatting（任务行 total_pnl） ---
  it("renders Total PnL with currency format", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    expect(screen.getByText("总盈亏")).toBeInTheDocument();
    expect(screen.getByText("$2,000")).toBeInTheDocument();
  });

  it("renders positive PnL with up trend", () => {
    render(
      <MetricsDashboard report={mockTradingReport} task={{ ...mockTradingTask, total_pnl: 500 }} />,
    );
    const pnlValue = screen.getByText("$500");
    expect(pnlValue).toBeInTheDocument();
    expect(pnlValue.closest("span")?.className).toContain("text-profit");
  });

  it("renders negative PnL with down trend", () => {
    render(
      <MetricsDashboard report={mockTradingReport} task={{ ...mockTradingTask, total_pnl: -300 }} />,
    );
    expect(screen.getByText("$-300")).toBeInTheDocument();
  });

  // --- Annualized return（报告 performance.annual_return） ---
  it("renders annualized return with percent format", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    expect(screen.getByText("年化收益")).toBeInTheDocument();
    expect(screen.getByText("+35.0%")).toBeInTheDocument();
  });

  // --- Win Rate ---
  it("renders Win Rate with up trend when >= 50%", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    // win_rate=0.6 -> "60.0%"
    expect(screen.getByText("60.0%")).toBeInTheDocument();
  });

  it("renders Win Rate with down trend when < 50%", () => {
    render(
      <MetricsDashboard
        report={{ ...mockTradingReport, performance: { ...mockTradingReport.performance, win_rate: 0.3 } }}
        task={mockTradingTask}
      />,
    );
    expect(screen.getByText("30.0%")).toBeInTheDocument();
  });

  it("renders Win Rate as - when null（零交易缺失语义）", () => {
    render(
      <MetricsDashboard
        report={{ ...mockTradingReport, performance: { ...mockTradingReport.performance, win_rate: null } }}
        task={mockTradingTask}
      />,
    );
    expect(screen.getByText("胜率")).toBeInTheDocument();
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  // --- Profit Factor ---
  it("renders Profit Factor with value", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    // profit_factor=1.8 -> "1.80"
    expect(screen.getByText("1.80")).toBeInTheDocument();
  });

  // --- Max Drawdown（负小数→百分位） ---
  it("renders Max Drawdown with negative sign", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    // max_drawdown=-0.008 -> "-0.8%"
    expect(screen.getByText("-0.8%")).toBeInTheDocument();
  });

  // --- Trades（任务行胜负计数） ---
  it("renders Trades with W/L format", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    // win_count=3, loss_count=2 -> "3胜/2负"
    expect(screen.getByText("3胜/2负")).toBeInTheDocument();
  });

  // --- All 6 labels present ---
  it("renders all 6 stat labels", () => {
    render(<MetricsDashboard report={mockTradingReport} task={mockTradingTask} />);
    const labels = ["总盈亏", "年化收益", "胜率", "盈亏比", "最大回撤", "交易次数"];
    for (const label of labels) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});
