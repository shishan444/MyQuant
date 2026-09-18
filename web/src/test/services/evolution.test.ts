/**
 * Tests for services/evolution.ts: evolution task API service functions.
 * Validates request construction and response mapping.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock axios before importing services
vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { api } from "@/services/api";
import {
  getEvolutionTasks,
  getEvolutionTask,
  createEvolutionTask,
  pauseEvolutionTask,
  stopEvolutionTask,
  resumeEvolutionTask,
  getEvolutionHistory,
} from "@/services/evolution";

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);

describe("evolution service", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --- getEvolutionTasks ---
  it("getEvolutionTasks calls correct endpoint with params", async () => {
    const mockResponse = { items: [], total: 0 };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getEvolutionTasks({ status: "running", page: 1 });
    expect(mockedGet).toHaveBeenCalledWith("/api/evolution/tasks", {
      params: { status: "running", page: 1 },
    });
    expect(result).toEqual(mockResponse);
  });

  // --- getEvolutionTask ---
  it("getEvolutionTask calls correct endpoint", async () => {
    const mockTask = { task_id: "t1", status: "running" };
    mockedGet.mockResolvedValueOnce(mockTask);

    const result = await getEvolutionTask("t1");
    expect(mockedGet).toHaveBeenCalledWith("/api/evolution/tasks/t1");
    expect(result.task_id).toBe("t1");
  });

  // --- createEvolutionTask ---
  it("createEvolutionTask posts correct payload", async () => {
    const mockTask = { task_id: "t2", status: "pending" };
    const payload = {
      symbol: "BTCUSDT",
      timeframe: "4h",
      population_size: 20,
      max_generations: 50,
    };
    mockedPost.mockResolvedValueOnce(mockTask);

    const result = await createEvolutionTask(payload);
    expect(mockedPost).toHaveBeenCalledWith("/api/evolution/tasks", { json: payload });
    expect(result.task_id).toBe("t2");
  });

  // --- pause/stop/resume ---
  it("pauseEvolutionTask calls correct endpoint", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "t1", status: "paused" });
    await pauseEvolutionTask("t1");
    expect(mockedPost).toHaveBeenCalledWith("/api/evolution/tasks/t1/pause");
  });

  it("stopEvolutionTask calls correct endpoint", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "t1", status: "stopped" });
    await stopEvolutionTask("t1");
    expect(mockedPost).toHaveBeenCalledWith("/api/evolution/tasks/t1/stop");
  });

  it("resumeEvolutionTask calls correct endpoint", async () => {
    mockedPost.mockResolvedValueOnce({ task_id: "t1", status: "running" });
    await resumeEvolutionTask("t1");
    expect(mockedPost).toHaveBeenCalledWith("/api/evolution/tasks/t1/resume");
  });

  // --- getEvolutionHistory (response mapping) ---
  it("getEvolutionHistory maps backend 'generations' to 'records'", async () => {
    const generations = [
      { generation: 1, best_score: 50, avg_score: 40, best_fitness: 0.45, avg_fitness: 0.35, created_at: "2024-01-01" },
      { generation: 2, best_score: 55, avg_score: 45, best_fitness: 0.52, avg_fitness: 0.40, created_at: "2024-01-02" },
    ];
    mockedGet.mockResolvedValueOnce({ task_id: "t1", generations });

    const result = await getEvolutionHistory("t1");
    expect(result.records).toHaveLength(2);
    expect(result.total).toBe(2);
    expect(result.records[0].generation).toBe(1);
  });

  it("getEvolutionHistory handles empty generations", async () => {
    mockedGet.mockResolvedValueOnce({ task_id: "t1", generations: [] });

    const result = await getEvolutionHistory("t1");
    expect(result.records).toHaveLength(0);
    expect(result.total).toBe(0);
  });
});
