import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock react-router
const mockNavigate = vi.fn();
let mockLocationState: Record<string, unknown> | null = null;

vi.mock("react-router", () => ({
  useLocation: () => ({ state: mockLocationState }),
  useNavigate: () => mockNavigate,
}));

// Mock API services
vi.mock("@/services/strategies", () => ({
  runBacktest: vi.fn(),
}));

vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(() => Promise.resolve({ data: [] })),
  getChartIndicators: vi.fn(() => Promise.resolve({
    ema: {}, boll: null, rsi: null, macd: null, kdj: null,
  })),
}));

vi.mock("@/stores/chart-settings", () => ({
  useChartSettings: vi.fn(() => ({
    emaList: [
      { period: 10, color: "#3B82F6", enabled: true },
    ],
    boll: { enabled: true, period: 20, std: 2.0, color: "#F59E0B" },
    rsi: { enabled: true, period: 14, overbought: 70, oversold: 30 },
    vol: { enabled: true, position: "overlay" },
    addEma: vi.fn(),
    removeEma: vi.fn(),
    updateEma: vi.fn(),
    reorderEma: vi.fn(),
    setBoll: vi.fn(),
    setRsi: vi.fn(),
    setVol: vi.fn(),
    resetToDefaults: vi.fn(),
    getEmaPeriods: vi.fn(() => [10]),
    getBollParams: vi.fn(() => ({ period: 20, std: 2 })),
    getIndicatorParams: vi.fn(),
  })),
}));

vi.mock("@/hooks/useStrategies", () => ({
  useCreateStrategy: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useStrategies: vi.fn(() => ({
    data: { items: [], total: 0 },
  })),
}));

vi.mock("@/hooks/useValidation", () => ({
  useValidateRules: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("@/hooks/useDatasets", () => ({
  useAvailableSources: vi.fn(() => ({
    queryKey: ["test"],
    queryFn: vi.fn(),
    staleTime: 5000,
  })),
}));

vi.mock("@/hooks/useChartIndicators", () => ({
  useChartIndicators: vi.fn(() => ({
    candleData: undefined,
    chartIndicators: [],
    chartBollData: undefined,
    volumeData: [],
    macdData: null,
    kdjData: null,
    isLoadingOhlcv: false,
    isLoadingIndicators: false,
  })),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("framer-motion", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

import { Lab } from "@/pages/Lab";
import { runBacktest } from "@/services/strategies";
import { mockDNA } from "@/test/fixtures";

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
  mockLocationState = null;
  vi.clearAllMocks();
});

describe("Lab - autoRun removal", () => {
  it("does NOT auto-run backtest when navigated from Evolution with DNA", () => {
    // Simulate navigation from Evolution page
    mockLocationState = {
      dna: mockDNA,
      symbol: "BTCUSDT",
      timeframe: "4h",
      dataStart: "2025-01-01",
      dataEnd: "2025-03-01",
    };

    render(<Lab />, { wrapper: createWrapper() });

    // runBacktest should NOT be called on mount
    expect(runBacktest).not.toHaveBeenCalled();
  });

  it("shows backtest mode when arriving with DNA route state", () => {
    mockLocationState = {
      dna: mockDNA,
      symbol: "BTCUSDT",
      timeframe: "4h",
      dataStart: "2025-01-01",
      dataEnd: "2025-03-01",
    };

    render(<Lab />, { wrapper: createWrapper() });

    // Should be in backtest mode (tab active)
    expect(screen.getByText("策略回测")).toBeInTheDocument();
  });

  it("defaults to hypothesis mode when no route state", () => {
    mockLocationState = null;

    render(<Lab />, { wrapper: createWrapper() });

    // Should show hypothesis mode UI elements
    expect(screen.getByText("假设验证")).toBeInTheDocument();
    expect(screen.getByText("规律构建器")).toBeInTheDocument();
  });

  it("shows collapsed config summary with symbol when in backtest mode with DNA", () => {
    mockLocationState = {
      dna: mockDNA,
      symbol: "BTCUSDT",
      timeframe: "4h",
    };

    render(<Lab />, { wrapper: createWrapper() });

    // Config panel should be collapsed (showing summary) with the symbol
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    // runBacktest should NOT have been called (user needs to click)
    expect(runBacktest).not.toHaveBeenCalled();
  });

  it("has three mode tabs: hypothesis, backtest, scene", () => {
    render(<Lab />, { wrapper: createWrapper() });

    expect(screen.getByText("假设验证")).toBeInTheDocument();
    expect(screen.getByText("策略回测")).toBeInTheDocument();
    expect(screen.getByText("场景验证")).toBeInTheDocument();
  });
});
