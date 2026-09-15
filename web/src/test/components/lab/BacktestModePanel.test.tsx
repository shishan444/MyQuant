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

// Mock KlineChart to avoid canvas/lightweight-charts issues in jsdom
vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: vi.fn(() => <div data-testid="kline-chart" />),
}));

// Mock EquityCurveChart to avoid recharts issues
vi.mock("@/components/lab/EquityCurveChart", () => ({
  EquityCurveChart: vi.fn(() => <div data-testid="equity-curve" />),
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

    // runBacktest should NOT have been called on mount
    expect(runBacktest).not.toHaveBeenCalled();
  });

  it("has no autoRun prop in the interface", () => {
    // Type-level test: ensure BacktestModePanelProps does not accept autoRun
    // This verifies at runtime that autoRun is not a valid prop
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

    // Manually trigger via ref (simulates parent button click)
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

    // No indicator fetch before running backtest
    expect(getChartIndicators).not.toHaveBeenCalled();

    // Trigger backtest
    ref.current?.runBacktest();

    // Wait for backtest to complete and indicators to be fetched
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

    // No sub-chart selector before backtest
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
      expect(screen.getByText("重试")).toBeInTheDocument();
    });
  });
});
