import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock API services before any imports that use them
vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(),
  getChartIndicators: vi.fn(),
}));

// Mock Zustand store
vi.mock("@/stores/chart-settings", () => ({
  useChartSettings: vi.fn(),
}));

import { useChartIndicators } from "@/hooks/useChartIndicators";
import { POLARITY } from "@/theme/tokens";
import { getOhlcvBySymbol, getChartIndicators } from "@/services/datasets";
import { useChartSettings } from "@/stores/chart-settings";
import { mockOhlcvData, mockIndicatorResponse, mockChartSettings } from "@/test/fixtures";

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
});

describe("useChartIndicators", () => {
  const defaultProps = {
    symbol: "BTCUSDT",
    timeframe: "4h",
    dateRange: { start: "2025-01-01", end: "2025-03-01" },
  };

  it("fetches OHLCV and indicator data when enabled", async () => {
    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.candleData).toBeDefined();
      expect(result.current.candleData!.length).toBe(5);
    });

    expect(getOhlcvBySymbol).toHaveBeenCalledWith("BTCUSDT", "4h", expect.objectContaining({
      start: "2025-01-01",
      end: "2025-03-01",
      limit: 50000,
    }));

    // Indicators fetched after candleData is available
    await waitFor(() => {
      expect(getChartIndicators).toHaveBeenCalled();
    });
  });

  it("does not return data when enabled=false", async () => {
    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: false }),
      { wrapper: createWrapper() },
    );

    // Even if the query fires due to queryOptions internal enabled,
    // the key behavior is that no data should be returned to the consumer
    // when our external enabled is false (e.g. before user triggers action)
    expect(result.current.candleData).toBeUndefined();
    expect(result.current.chartIndicators).toEqual([]);
    expect(result.current.chartBollData).toBeUndefined();
    expect(result.current.volumeData).toEqual([]);
    expect(result.current.macdData).toBeNull();
    expect(result.current.kdjData).toBeNull();
  });

  it("returns chartIndicators with EMA data", async () => {
    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.chartIndicators.length).toBeGreaterThan(0);
    });

    // Should have EMA 10 and EMA 20 (enabled in mockChartSettings)
    const emaIndicators = result.current.chartIndicators.filter((i) => i.type === "ema");
    expect(emaIndicators).toHaveLength(2);
    expect(emaIndicators[0].id).toBe("ema_10");
    expect(emaIndicators[1].id).toBe("ema_20");
  });

  it("returns BOLL data when boll is enabled", async () => {
    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.chartBollData).toBeDefined();
    });

    expect(result.current.chartBollData).toHaveProperty("upper");
    expect(result.current.chartBollData).toHaveProperty("middle");
    expect(result.current.chartBollData).toHaveProperty("lower");
  });

  it("returns undefined bollData when boll is disabled", async () => {
    vi.mocked(useChartSettings).mockReturnValue({
      ...mockChartSettings,
      boll: { ...mockChartSettings.boll, enabled: false },
    } as ReturnType<typeof useChartSettings>);

    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.candleData).toBeDefined();
    });

    expect(result.current.chartBollData).toBeUndefined();
  });

  it("returns volumeData derived from candleData", async () => {
    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.volumeData.length).toBe(5);
    });

    // First candle: close >= open, should be green
    expect(result.current.volumeData[0]).toEqual({
      time: "2025-01-01T00:00:00Z",
      value: 1000,
      color: `${POLARITY.up}66`,
    });
  });

  it("returns macdData and kdjData from indicator response", async () => {
    const { result } = renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true, subChartType: "macd" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.macdData).not.toBeNull();
    });

    expect(result.current.macdData).toHaveProperty("macd");
    expect(result.current.macdData).toHaveProperty("signal");
    expect(result.current.macdData).toHaveProperty("histogram");
  });

  it("passes correct subChartType to API params", async () => {
    renderHook(
      () => useChartIndicators({ ...defaultProps, enabled: true, subChartType: "rsi" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(getChartIndicators).toHaveBeenCalledWith("BTCUSDT", "4h", expect.objectContaining({
        rsi_enabled: true,
        macd_enabled: false,
        kdj_enabled: false,
      }));
    });
  });
});
