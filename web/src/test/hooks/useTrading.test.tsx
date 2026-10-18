/**
 * Tests for hooks/useTrading.ts: query key factory + mutation hooks.
 * （新域形制：create 收 strategy_id+覆写；restart→clone）
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
  cloneTradingTask: vi.fn(),
  getTradingFills: vi.fn(),
  getTradingFunding: vi.fn(),
  getTradingRunnerStatus: vi.fn(),
  getTradingEquity: vi.fn(),
  getTradingReport: vi.fn(),
  getTradingFinalReport: vi.fn(),
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
  useCloneTradingTask,
  useDeleteTradingTask,
  isTerminalStatus,
} from "@/hooks/useTrading";
import {
  listTradingTasks,
  createTradingTask,
  stopTradingTask,
  cloneTradingTask,
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

describe("isTerminalStatus（终态门槛——clone/delete/结题报告）", () => {
  it("completed/stopped/error 为终态", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("stopped")).toBe(true);
    expect(isTerminalStatus("error")).toBe(true);
  });

  it("pending/running 非终态", () => {
    expect(isTerminalStatus("pending")).toBe(false);
    expect(isTerminalStatus("running")).toBe(false);
  });
});

describe("useTrading hooks", () => {
  // --- useTradingTasks ---
  describe("useTradingTasks", () => {
    it("fetches task list on mount", async () => {
      const mockData = {
        tasks: [{
          task_id: "t1", status: "stopped" as const, strategy_id: "s1", symbol: "BTCUSDT",
          timeframe: "4h", initial_cash: 100000, fee: 0.001, slippage: 0.0005,
          leverage: 1, window_bars: 180, stop_loss_pct: null, bars_processed: 100,
          last_bar_time: null, balance: null, position_side: null, position_entry: null,
          position_quantity: null, position_margin: null, position_open_cost: null,
          position_sl_price: null, position_tp_price: null, position_trail_peak: null,
          position_entry_bar_seq: null, unrealized_pnl: null, total_trades: 0,
          win_count: 0, loss_count: 0, total_pnl: 0,
          research_status_snapshot: "researched",
          created_at: "2025-01-01T00:00:00Z", updated_at: "2025-01-01T00:00:00Z",
          started_at: null, stopped_at: null, stop_reason: null, heartbeat_at: null,
          error_detail: null,
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

  // --- useCreateTradingTask（strategy_id+可选覆写） ---
  describe("useCreateTradingTask", () => {
    it("calls createTradingTask and invalidates queries on success", async () => {
      const params = { strategy_id: "s1", initial_cash: 200_000, window_bars: null };
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

  // --- useCloneTradingTask（原 restart 调用面） ---
  describe("useCloneTradingTask", () => {
    it("calls cloneTradingTask with taskId（新任务编号返回）", async () => {
      const mockTask = { task_id: "t2", status: "pending" };
      vi.mocked(cloneTradingTask).mockResolvedValueOnce(mockTask as any);

      const { result } = renderHook(() => useCloneTradingTask(), { wrapper: createWrapper() });

      result.current.mutate("t1");

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(cloneTradingTask).toHaveBeenCalledWith("t1");
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
