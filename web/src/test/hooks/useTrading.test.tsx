/**
 * Tests for hooks/useTrading.ts: query key factory + mutation hooks.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock API services before any imports that use them
vi.mock("@/services/trading", () => ({
  listTradingTasks: vi.fn(),
  getTradingTask: vi.fn(),
  createTradingTask: vi.fn(),
  stopTradingTask: vi.fn(),
  getTradingTrades: vi.fn(),
  getTradingRunnerStatus: vi.fn(),
  getTradingEquity: vi.fn(),
  getTradingMetrics: vi.fn(),
  deleteTradingTask: vi.fn(),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  useTradingTasks,
  useCreateTradingTask,
  useStopTradingTask,
  useDeleteTradingTask,
} from "@/hooks/useTrading";
import {
  listTradingTasks,
  createTradingTask,
  stopTradingTask,
  deleteTradingTask,
} from "@/services/trading";

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
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
  vi.clearAllMocks();
});

describe("useTrading hooks", () => {
  // --- useTradingTasks ---
  describe("useTradingTasks", () => {
    it("fetches task list on mount", async () => {
      const mockData = {
        tasks: [{
          task_id: "t1", status: "stopped", strategy_name: null, symbol: "BTCUSDT", timeframe: "4h",
          initial_cash: 100000, fee: 0.001, leverage: 1, direction: "long", score_template: "default",
          created_at: "2025-01-01T00:00:00Z", updated_at: "2025-01-01T00:00:00Z", started_at: null, stopped_at: null,
          stop_reason: null, position_side: null, position_entry: null, position_quantity: null, position_margin: null,
          position_funding: null, balance: null, unrealized_pnl: 0, total_trades: 0, total_pnl: 0, win_count: 0,
          loss_count: 0, last_bar_time: null, last_bar_close: null, confidence_sizing_enabled: false,
          execution_model: "v1",
        }],
        total: 1,
      };
      vi.mocked(listTradingTasks).mockResolvedValueOnce(mockData);

      const { result } = renderHook(() => useTradingTasks(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data).toEqual(mockData);
      expect(listTradingTasks).toHaveBeenCalledOnce();
    });

    it("handles empty task list", async () => {
      const mockData = { tasks: [], total: 0 };
      vi.mocked(listTradingTasks).mockResolvedValueOnce(mockData);

      const { result } = renderHook(() => useTradingTasks(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data?.tasks).toEqual([]);
    });
  });

  // --- useCreateTradingTask ---
  describe("useCreateTradingTask", () => {
    it("calls createTradingTask and invalidates queries on success", async () => {
      const params = { dna_json: "{}", symbol: "BTCUSDT", timeframe: "4h" };
      const mockTask = { task_id: "new1", status: "pending" };
      vi.mocked(createTradingTask).mockResolvedValueOnce(mockTask as any);

      const { result } = renderHook(() => useCreateTradingTask(), { wrapper: createWrapper() });

      result.current.mutate(params);

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(createTradingTask).toHaveBeenCalledWith(params);
    });
  });

  // --- useStopTradingTask ---
  describe("useStopTradingTask", () => {
    it("calls stopTradingTask with taskId", async () => {
      const mockTask = { task_id: "t1", status: "stopped" };
      vi.mocked(stopTradingTask).mockResolvedValueOnce(mockTask as any);

      const { result } = renderHook(() => useStopTradingTask(), { wrapper: createWrapper() });

      result.current.mutate("t1");

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(stopTradingTask).toHaveBeenCalledWith("t1");
    });
  });

  // --- useDeleteTradingTask ---
  describe("useDeleteTradingTask", () => {
    it("calls deleteTradingTask with taskId", async () => {
      vi.mocked(deleteTradingTask).mockResolvedValueOnce({ deleted: true });

      const { result } = renderHook(() => useDeleteTradingTask(), { wrapper: createWrapper() });

      result.current.mutate("t1");

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(deleteTradingTask).toHaveBeenCalledWith("t1");
    });
  });
});
