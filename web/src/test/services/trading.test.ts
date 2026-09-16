/**
 * Tests for services/trading.ts: trading task API service functions.
 * Validates request construction and response mapping.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock axios before importing services
vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

import { api } from "@/services/api";
import {
  listTradingTasks,
  getTradingTask,
  createTradingTask,
  stopTradingTask,
  getTradingTrades,
  getTradingRunnerStatus,
  getTradingEquity,
  getTradingMetrics,
  deleteTradingTask,
} from "@/services/trading";

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);
const mockedDelete = vi.mocked(api.delete);

describe("trading service", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --- listTradingTasks ---
  it("listTradingTasks calls correct endpoint with params", async () => {
    const mockResponse = { tasks: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await listTradingTasks({ status: "running", limit: 10, offset: 0 });
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks", {
      params: { status: "running", limit: 10, offset: 0 },
    });
    expect(result).toEqual(mockResponse);
  });

  it("listTradingTasks works without params", async () => {
    const mockResponse = { tasks: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await listTradingTasks();
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks", { params: undefined });
    expect(result).toEqual(mockResponse);
  });

  // --- getTradingTask ---
  it("getTradingTask calls correct endpoint", async () => {
    const mockTask = { task_id: "t1", status: "running" };
    mockedGet.mockResolvedValueOnce(mockTask);

    const result = await getTradingTask("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks/t1");
    expect(result).toEqual(mockTask);
  });

  // --- createTradingTask ---
  it("createTradingTask calls POST with correct params", async () => {
    const params = { dna_json: '{"signal_genes":[]}', symbol: "BTCUSDT", timeframe: "4h" };
    const mockTask = { task_id: "t2", status: "pending", ...params };
    mockedPost.mockResolvedValueOnce(mockTask);

    const result = await createTradingTask(params);
    expect(mockedPost).toHaveBeenCalledWith("/api/trading/tasks", { json: params });
    expect(result).toEqual(mockTask);
  });

  // --- stopTradingTask ---
  it("stopTradingTask calls POST with correct endpoint", async () => {
    const mockTask = { task_id: "t1", status: "stopped" };
    mockedPost.mockResolvedValueOnce(mockTask);

    const result = await stopTradingTask("t1");
    expect(mockedPost).toHaveBeenCalledWith("/api/trading/tasks/t1/stop");
    expect(result).toEqual(mockTask);
  });

  // --- getTradingTrades ---
  it("getTradingTrades calls correct endpoint with limit", async () => {
    const mockResponse = { trades: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getTradingTrades("t1", 50);
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks/t1/trades", {
      params: { limit: 50 },
    });
    expect(result).toEqual(mockResponse);
  });

  it("getTradingTrades works without limit", async () => {
    const mockResponse = { trades: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getTradingTrades("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks/t1/trades", {
      params: { limit: undefined },
    });
    expect(result).toEqual(mockResponse);
  });

  // --- getTradingRunnerStatus ---
  it("getTradingRunnerStatus calls correct endpoint", async () => {
    const mockStatus = { is_alive: true, active_task_id: "t1" };
    mockedGet.mockResolvedValueOnce(mockStatus);

    const result = await getTradingRunnerStatus();
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/runner-status");
    expect(result).toEqual(mockStatus);
  });

  // --- getTradingEquity ---
  it("getTradingEquity calls correct endpoint", async () => {
    const mockEquity = { snapshots: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockEquity);

    const result = await getTradingEquity("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks/t1/equity");
    expect(result).toEqual(mockEquity);
  });

  // --- getTradingMetrics ---
  it("getTradingMetrics calls correct endpoint", async () => {
    const mockMetrics = { task_id: "t1", total_return: 0.1, win_rate: 0.6 };
    mockedGet.mockResolvedValueOnce(mockMetrics);

    const result = await getTradingMetrics("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/trading/tasks/t1/metrics");
    expect(result).toEqual(mockMetrics);
  });

  // --- deleteTradingTask ---
  it("deleteTradingTask calls DELETE with correct endpoint", async () => {
    const mockResponse = { deleted: true };
    mockedDelete.mockResolvedValueOnce(mockResponse);

    const result = await deleteTradingTask("t1");
    expect(mockedDelete).toHaveBeenCalledWith("/api/trading/tasks/t1");
    expect(result).toEqual(mockResponse);
  });
});
