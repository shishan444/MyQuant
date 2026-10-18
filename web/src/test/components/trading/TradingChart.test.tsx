/**
 * Tests for TradingChart component and fillsToSignals logic.
 * Covers: signal conversion (kind 九值归并), loading state, empty fills.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { PaperFill } from "@/services/trading";

// Mock hooks and child components
vi.mock("@/hooks/useChartIndicators", () => ({
  useChartIndicators: vi.fn(),
}));

vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: vi.fn(({ signals }) => (
    <div data-testid="kline-chart">
      <span data-testid="signal-count">{signals?.length ?? 0}</span>
    </div>
  )),
}));

import { TradingChart } from "@/components/trading/TradingChart";
import { useChartIndicators } from "@/hooks/useChartIndicators";

const mockUseChartIndicators = vi.mocked(useChartIndicators);

function makeFill(overrides: Partial<PaperFill> = {}): PaperFill {
  return {
    id: 1,
    task_id: "t1",
    bar_time: "2025-01-05T08:00:00Z",
    seq: 3,
    kind: "open",
    side: "long",
    price: 100000,
    quantity: 0.1,
    fee: 10,
    realized_pnl: null,
    reason: null,
    ...overrides,
  };
}

describe("TradingChart", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseChartIndicators.mockReturnValue({
      candleData: [{ timestamp: "2025-01-05T08:00:00Z", open: 100, high: 110, low: 95, close: 105, volume: 1000 }],
      volumeData: [],
      isLoadingOhlcv: false,
    } as any);
  });

  // --- fillsToSignals: open -> buy 侧信号 ---
  it("converts open fills to signals", () => {
    const fills = [makeFill({ kind: "open", bar_time: "2025-01-05T08:00:00Z" })];
    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={fills} />);
    expect(screen.getByTestId("signal-count").textContent).toBe("1");
  });

  // --- 出场五值（exit_*）归并 close 侧信号 ---
  it("converts exit_sl fills to signals (归并 close)", () => {
    const fills = [makeFill({ kind: "exit_sl", side: "short", bar_time: "2025-01-10T12:00:00Z" })];
    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={fills} />);
    expect(screen.getByTestId("signal-count").textContent).toBe("1");
  });

  // --- 出场类 side=null 不画信号（与旧口径一致——filter side） ---
  it("skips exit fills with null side", () => {
    const fills = [makeFill({ kind: "exit_sl", side: null })];
    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={fills} />);
    expect(screen.getByTestId("signal-count").textContent).toBe("0");
  });

  // --- includes add/reduce actions as signals ---
  it("includes fills with kind other than open as signals", () => {
    const fills = [
      makeFill({ id: 1, kind: "add", bar_time: "2025-01-05T08:00:00Z" }),
      makeFill({ id: 2, kind: "reduce", bar_time: "2025-01-06T08:00:00Z" }),
    ];
    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={fills} />);
    expect(screen.getByTestId("signal-count").textContent).toBe("2");
  });

  // --- Empty fills ---
  it("handles empty fills array", () => {
    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={[]} />);
    expect(screen.getByTestId("signal-count").textContent).toBe("0");
  });

  // --- Mixed fills ---
  it("correctly maps mixed open and exit fills", () => {
    const fills = [
      makeFill({ id: 1, kind: "open", bar_time: "2025-01-05T08:00:00Z" }),
      makeFill({ id: 2, kind: "exit_tp", side: "short", bar_time: "2025-01-10T12:00:00Z" }),
      makeFill({ id: 3, kind: "open", bar_time: "2025-01-15T08:00:00Z" }),
      makeFill({ id: 4, kind: "exit_liquidation", side: "short", bar_time: "2025-01-20T12:00:00Z" }),
    ];
    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={fills} />);
    expect(screen.getByTestId("signal-count").textContent).toBe("4");
  });

  // --- Loading state ---
  it("shows loading state when ohlcv is loading", () => {
    mockUseChartIndicators.mockReturnValue({
      candleData: null,
      volumeData: null,
      isLoadingOhlcv: true,
    } as any);

    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={[]} />);
    expect(screen.getByText("Loading chart...")).toBeInTheDocument();
  });

  // --- Empty candle data ---
  it("shows loading state when candle data is empty", () => {
    mockUseChartIndicators.mockReturnValue({
      candleData: [],
      volumeData: [],
      isLoadingOhlcv: false,
    } as any);

    render(<TradingChart symbol="BTCUSDT" timeframe="4h" fills={[]} />);
    expect(screen.getByText("Loading chart...")).toBeInTheDocument();
  });
});
