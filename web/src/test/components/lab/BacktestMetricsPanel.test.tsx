import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { BacktestMetricsPanel } from "@/components/lab/BacktestMetricsPanel";
import { mockBacktestResult } from "@/test/fixtures";
import type { BacktestResult } from "@/types/api";

// 基准对照展示（improve-backtest-benchmark-disclosure 4.2）：
// 新三卡（基准收益/超额收益/年化超额）条件渲染——字段非 null/undefined 才出卡，
// 旧后端形制（无新键/None→null）三卡不渲染，原五项不受影响。

describe("BacktestMetricsPanel", () => {
  it("含基准对照字段时渲染基准/超额/年化超额三卡", () => {
    render(<BacktestMetricsPanel result={mockBacktestResult} />);

    // 新三卡 label 与数值可见（fixtures: benchmark 0.42 / excess -0.165 / alpha -0.21）
    expect(screen.getByText("基准收益")).toBeInTheDocument();
    expect(screen.getByText("42.00%")).toBeInTheDocument();
    expect(screen.getByText("超额收益")).toBeInTheDocument();
    expect(screen.getByText("-16.50%")).toBeInTheDocument();
    expect(screen.getByText("年化超额")).toBeInTheDocument();
    expect(screen.getByText("-21.00%")).toBeInTheDocument();
  });

  it("超额正负对齐总收益语义色，基准用中性色", () => {
    render(<BacktestMetricsPanel result={mockBacktestResult} />);

    // 负超额 = 红（与负总收益同形制）
    expect(screen.getByText("-16.50%").className).toContain("text-red-400");
    expect(screen.getByText("-21.00%").className).toContain("text-red-400");
    // 基准 = 中性色
    expect(screen.getByText("42.00%").className).toContain("text-slate-200");

    // 正超额 = 绿
    render(
      <BacktestMetricsPanel
        result={{
          ...mockBacktestResult,
          benchmark_return: 0.1,
          excess_return: 0.155,
          alpha: 0.08,
        }}
      />,
    );
    expect(screen.getByText("15.50%").className).toContain("text-emerald-400");
    expect(screen.getByText("8.00%").className).toContain("text-emerald-400");
  });

  it("旧后端形制（无新字段）三卡不渲染，原五项正常", () => {
    // 结构里彻底没有新键（旧响应 JSON 形制）
    const legacyResult: BacktestResult = JSON.parse(JSON.stringify(mockBacktestResult));
    delete legacyResult.benchmark_return;
    delete legacyResult.excess_return;
    delete legacyResult.alpha;

    render(<BacktestMetricsPanel result={legacyResult} />);

    expect(screen.queryByText("基准收益")).not.toBeInTheDocument();
    expect(screen.queryByText("超额收益")).not.toBeInTheDocument();
    expect(screen.queryByText("年化超额")).not.toBeInTheDocument();

    // 原五项向后兼容
    expect(screen.getByText("总收益")).toBeInTheDocument();
    expect(screen.getByText("夏普比率")).toBeInTheDocument();
    expect(screen.getByText("最大回撤")).toBeInTheDocument();
    expect(screen.getByText("胜率")).toBeInTheDocument();
    expect(screen.getByText("交易次数")).toBeInTheDocument();
  });

  it("新字段为 null（后端 None）时同样不渲染三卡", () => {
    render(
      <BacktestMetricsPanel
        result={{
          ...mockBacktestResult,
          benchmark_return: null,
          excess_return: null,
          alpha: null,
        }}
      />,
    );

    expect(screen.queryByText("基准收益")).not.toBeInTheDocument();
    expect(screen.queryByText("超额收益")).not.toBeInTheDocument();
    expect(screen.queryByText("年化超额")).not.toBeInTheDocument();
    expect(screen.getByText("总收益")).toBeInTheDocument();
  });
});
