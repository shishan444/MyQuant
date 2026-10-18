/**
 * Tests for services/trading.ts: paper-trading API service functions.
 * Validates request construction and response mapping（/api/paper-trading 十二端点）.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock api client before importing services
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
  cloneTradingTask,
  getTradingFills,
  getTradingFunding,
  getTradingRunnerStatus,
  getTradingEquity,
  getTradingReport,
  getTradingFinalReport,
  deleteTradingTask,
} from "@/services/trading";

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);
const mockedDelete = vi.mocked(api.delete);

describe("trading service（/api/paper-trading 新域）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --- listTradingTasks ---
  it("listTradingTasks calls correct endpoint with params", async () => {
    const mockResponse = { tasks: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await listTradingTasks({ status: "running", limit: 10, offset: 0 });
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks", {
      params: { status: "running", limit: 10, offset: 0 },
    });
    expect(result).toEqual(mockResponse);
  });

  it("listTradingTasks works without params", async () => {
    const mockResponse = { tasks: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await listTradingTasks();
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks", { params: undefined });
    expect(result).toEqual(mockResponse);
  });

  // --- getTradingTask ---
  it("getTradingTask calls correct endpoint", async () => {
    const mockTask = { task_id: "t1", status: "running" };
    mockedGet.mockResolvedValueOnce(mockTask);

    const result = await getTradingTask("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1");
    expect(result).toEqual(mockTask);
  });

  // --- createTradingTask（上盘流转：strategy_id+可选覆写） ---
  it("createTradingTask calls POST with strategy_id and overrides", async () => {
    const params = { strategy_id: "s1", initial_cash: 200_000, window_bars: null };
    const mockTask = { task_id: "t2", status: "pending", ...params };
    mockedPost.mockResolvedValueOnce(mockTask);

    const result = await createTradingTask(params);
    expect(mockedPost).toHaveBeenCalledWith("/api/paper-trading/tasks", { json: params });
    expect(result).toEqual(mockTask);
  });

  // --- stopTradingTask ---
  it("stopTradingTask calls POST with correct endpoint", async () => {
    const mockTask = { task_id: "t1", status: "stopped" };
    mockedPost.mockResolvedValueOnce(mockTask);

    const result = await stopTradingTask("t1");
    expect(mockedPost).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/stop");
    expect(result).toEqual(mockTask);
  });

  // --- cloneTradingTask（原 restart 调用面换 clone） ---
  it("cloneTradingTask calls POST with correct endpoint", async () => {
    const mockTask = { task_id: "t3", status: "pending" };
    mockedPost.mockResolvedValueOnce(mockTask);

    const result = await cloneTradingTask("t1");
    expect(mockedPost).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/clone");
    expect(result).toEqual(mockTask);
  });

  // --- getTradingFills（原 trades 调用面换 fills） ---
  it("getTradingFills calls correct endpoint with pagination", async () => {
    const mockResponse = { fills: [], limit: 500, offset: 0, has_more: false };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getTradingFills("t1", { limit: 500 });
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/fills", {
      params: { limit: 500, offset: undefined },
    });
    expect(result).toEqual(mockResponse);
  });

  it("getTradingFills works without params", async () => {
    const mockResponse = { fills: [], limit: 100, offset: 0, has_more: false };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getTradingFills("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/fills", {
      params: { limit: undefined, offset: undefined },
    });
    expect(result).toEqual(mockResponse);
  });

  // --- getTradingFunding（新增端点） ---
  it("getTradingFunding calls correct endpoint", async () => {
    const mockResponse = { records: [], limit: 100, offset: 0, has_more: false };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getTradingFunding("t1", { limit: 200, offset: 100 });
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/funding", {
      params: { limit: 200, offset: 100 },
    });
    expect(result).toEqual(mockResponse);
  });

  // --- getTradingRunnerStatus ---
  it("getTradingRunnerStatus calls correct endpoint", async () => {
    const mockStatus = {
      max_running_tasks: 2,
      running_count: 1,
      active_task_ids: ["t1"],
      pending_depth: 0,
    };
    mockedGet.mockResolvedValueOnce(mockStatus);

    const result = await getTradingRunnerStatus();
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/runner-status");
    expect(result).toEqual(mockStatus);
  });

  // --- getTradingEquity ---
  it("getTradingEquity calls correct endpoint", async () => {
    const mockEquity = { snapshots: [], limit: 500, offset: 0, has_more: false };
    mockedGet.mockResolvedValueOnce(mockEquity);

    const result = await getTradingEquity("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/equity", {
      params: { limit: undefined, offset: undefined },
    });
    expect(result).toEqual(mockEquity);
  });

  // --- getTradingReport（原 metrics 调用面换 report 三层报告） ---
  it("getTradingReport calls correct endpoint", async () => {
    const mockReport = { task_id: "t1", performance: {}, deviation: {}, evidence: {} };
    mockedGet.mockResolvedValueOnce(mockReport);

    const result = await getTradingReport("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/report");
    expect(result).toEqual(mockReport);
  });

  // --- getTradingFinalReport（新增端点） ---
  it("getTradingFinalReport calls correct endpoint with version", async () => {
    const mockFinal = { task_id: "t1", report_version: 1, generated_at: "ts", as_of: "ts", report: {} };
    mockedGet.mockResolvedValueOnce(mockFinal);

    const result = await getTradingFinalReport("t1", 1);
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/report/final", {
      params: { version: 1 },
    });
    expect(result).toEqual(mockFinal);
  });

  it("getTradingFinalReport omits version when not given", async () => {
    const mockFinal = { task_id: "t1", report_version: 2, generated_at: "ts", as_of: "ts", report: {} };
    mockedGet.mockResolvedValueOnce(mockFinal);

    await getTradingFinalReport("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/paper-trading/tasks/t1/report/final", {
      params: { version: undefined },
    });
  });

  // --- deleteTradingTask ---
  it("deleteTradingTask calls DELETE with correct endpoint", async () => {
    const mockResponse = { deleted: true };
    mockedDelete.mockResolvedValueOnce(mockResponse);

    const result = await deleteTradingTask("t1");
    expect(mockedDelete).toHaveBeenCalledWith("/api/paper-trading/tasks/t1");
    expect(result).toEqual(mockResponse);
  });
});
