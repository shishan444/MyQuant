import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock API services
vi.mock("@/services/strategies", () => ({
  runBacktest: vi.fn(),
}));

vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(),
  getChartIndicators: vi.fn(),
}));

vi.mock("@/stores/chart-settings", () => ({
  useChartSettings: vi.fn(),
}));

vi.mock("@/hooks/useStrategies", () => ({
  useCreateStrategy: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock KlineChart 规避 jsdom 下 canvas/lightweight-charts 问题
vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: vi.fn(() => <div data-testid="kline-chart" />),
}));

// Mock EquityCurve 规避 jsdom 下 echarts canvas 问题
vi.mock("@/components/charts/EquityCurve", () => ({
  EquityCurve: vi.fn(() => <div data-testid="equity-curve" />),
}));

// Mock StrategyDetail
vi.mock("@/components/StrategyDetail", () => ({
  StrategyDetail: vi.fn(() => <div data-testid="strategy-detail" />),
}));

// Mock BacktestMetricsPanel
vi.mock("@/components/lab/BacktestMetricsPanel", () => ({
  BacktestMetricsPanel: vi.fn(() => <div data-testid="backtest-metrics" />),
}));

import { BacktestModePanel } from "@/components/lab/BacktestModePanel";
import type { BacktestModePanelHandle } from "@/components/lab/BacktestModePanel";
import { runBacktest } from "@/services/strategies";
import { getOhlcvBySymbol, getChartIndicators } from "@/services/datasets";
import { useChartSettings } from "@/stores/chart-settings";
import { mockDNA, mockBacktestResult, mockOhlcvData, mockIndicatorResponse, mockChartSettings } from "@/test/fixtures";

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        {children}
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  vi.mocked(useChartSettings).mockReturnValue(mockChartSettings as ReturnType<typeof useChartSettings>);
  vi.mocked(getOhlcvBySymbol).mockResolvedValue(mockOhlcvData);
  vi.mocked(getChartIndicators).mockResolvedValue(mockIndicatorResponse);
  vi.mocked(runBacktest).mockResolvedValue(mockBacktestResult);
});

describe("BacktestModePanel", () => {
  const defaultProps = {
    dna: mockDNA,
    symbol: "BTCUSDT",
    timeframe: "4h",
    dataStart: "2025-01-01",
    dataEnd: "2025-03-01",
  };

  it("does NOT auto-run backtest on mount", () => {
    render(<BacktestModePanel {...defaultProps} />, { wrapper: createWrapper() });

    // 挂载时不应调用 runBacktest
    expect(runBacktest).not.toHaveBeenCalled();
  });

  it("has no autoRun prop in the interface", () => {
    // 类型级测试：确保 BacktestModePanelProps 不接受 autoRun
    // 运行时验证 autoRun 不是合法 prop
    const props = { ...defaultProps };
    expect(props).not.toHaveProperty("autoRun");
  });

  it("does not call runBacktest API when first rendered with DNA", () => {
    render(<BacktestModePanel {...defaultProps} />, { wrapper: createWrapper() });

    // Should show DNA summary but NOT trigger backtest
    expect(runBacktest).not.toHaveBeenCalled();
    expect(screen.getByText("策略基因")).toBeInTheDocument();
  });

  it("shows DNA strategy detail on mount", () => {
    render(<BacktestModePanel {...defaultProps} />, { wrapper: createWrapper() });

    expect(screen.getByText("策略基因")).toBeInTheDocument();
  });

  it("triggers backtest when runBacktest is called via ref", async () => {
    const ref = { current: null as any };

    render(
      <BacktestModePanel ref={(el) => { ref.current = el; }} {...defaultProps} />,
      { wrapper: createWrapper() },
    );

    // 经 ref 手动触发（模拟父组件按钮点击）
    ref.current?.runBacktest();

    await waitFor(() => {
      expect(runBacktest).toHaveBeenCalledWith(expect.objectContaining({
        dna: expect.any(Object),
        symbol: "BTCUSDT",
        timeframe: "4h",
      }));
    });
  });

  it("fetches chart indicators after backtest result is set", async () => {
    const ref = { current: null as any };

    render(
      <BacktestModePanel ref={(el) => { ref.current = el; }} {...defaultProps} />,
      { wrapper: createWrapper() },
    );

    // 回测运行前不拉取指标
    expect(getChartIndicators).not.toHaveBeenCalled();

    // 触发回测
    ref.current?.runBacktest();

    // 等回测完成并拉到指标
    await waitFor(() => {
      expect(getChartIndicators).toHaveBeenCalled();
    });
  });

  it("shows sub-chart selector after backtest result", async () => {
    const ref = { current: null as any };

    render(
      <BacktestModePanel ref={(el) => { ref.current = el; }} {...defaultProps} />,
      { wrapper: createWrapper() },
    );

    // 回测前无副图选择器
    expect(screen.queryByText("VOLUME")).not.toBeInTheDocument();

    ref.current?.runBacktest();

    await waitFor(() => {
      expect(screen.getByText("VOLUME")).toBeInTheDocument();
      expect(screen.getByText("MACD")).toBeInTheDocument();
      expect(screen.getByText("RSI")).toBeInTheDocument();
      expect(screen.getByText("KDJ")).toBeInTheDocument();
    });
  });

  it("shows backtest metrics after successful run", async () => {
    const ref = { current: null as any };

    render(
      <BacktestModePanel ref={(el) => { ref.current = el; }} {...defaultProps} />,
      { wrapper: createWrapper() },
    );

    ref.current?.runBacktest();

    await waitFor(() => {
      expect(screen.getByText("回测指标")).toBeInTheDocument();
      expect(screen.getByText("资金曲线")).toBeInTheDocument();
    });
  });

  it("shows error state and retry button when backtest fails", async () => {
    vi.mocked(runBacktest).mockRejectedValueOnce(new Error("API timeout"));

    const ref = { current: null as any };

    render(
      <BacktestModePanel ref={(el) => { ref.current = el; }} {...defaultProps} />,
      { wrapper: createWrapper() },
    );

    ref.current?.runBacktest();

    await waitFor(() => {
      expect(screen.getByText("API timeout")).toBeInTheDocument();
      expect(screen.getByText(/重\s*试/)).toBeInTheDocument();
    });
  });

  // -- onResult 回调透传（hypothesis-workbench 组 4.1：sl_series 等
  //    结果面透传宿主——Lab 页止损线画线的数据来源）--

  it("calls onResult with backtest result including sl_series after successful run", async () => {
    // 回传体携带 series 档止损线序列（bridge [{time,value}] 形制——
    // time 为空格分隔 ISO+UTC 偏移字面，见后端 test_sl_series_output）
    const slSeries = [
      { time: "2025-01-01 00:00:00+00:00", value: 95000 },
      { time: "2025-01-02 00:00:00+00:00", value: 94500 },
    ];
    vi.mocked(runBacktest).mockResolvedValueOnce({
      ...mockBacktestResult,
      sl_series: slSeries,
    });
    const onResult = vi.fn();
    const ref: { current: BacktestModePanelHandle | null } = { current: null };

    render(
      <BacktestModePanel
        ref={(el) => { ref.current = el; }}
        onResult={onResult}
        {...defaultProps}
      />,
      { wrapper: createWrapper() },
    );

    ref.current?.runBacktest();

    // 回调收到 API 返回体本身（sl_series 原样透传宿主）
    await waitFor(() => {
      expect(onResult).toHaveBeenCalledTimes(1);
    });
    expect(onResult).toHaveBeenCalledWith(
      expect.objectContaining({ sl_series: slSeries }));
  });

  it("does not call onResult when backtest fails", async () => {
    vi.mocked(runBacktest).mockRejectedValueOnce(new Error("API timeout"));
    const onResult = vi.fn();
    const ref: { current: BacktestModePanelHandle | null } = { current: null };

    render(
      <BacktestModePanel
        ref={(el) => { ref.current = el; }}
        onResult={onResult}
        {...defaultProps}
      />,
      { wrapper: createWrapper() },
    );

    ref.current?.runBacktest();

    await waitFor(() => {
      expect(screen.getByText("API timeout")).toBeInTheDocument();
    });
    // 失败路径禁止面：无结果不回调（宿主不会拿旧/空结果画线）
    expect(onResult).not.toHaveBeenCalled();
  });

  it("runs backtest without onResult prop without crashing", async () => {
    // onResult 可选语义：宿主不消费时不影响回测主流程
    const ref: { current: BacktestModePanelHandle | null } = { current: null };

    render(
      <BacktestModePanel ref={(el) => { ref.current = el; }} {...defaultProps} />,
      { wrapper: createWrapper() },
    );

    ref.current?.runBacktest();

    await waitFor(() => {
      expect(screen.getByText("回测指标")).toBeInTheDocument();
    });
  });
});
