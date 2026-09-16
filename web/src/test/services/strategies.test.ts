/**
 * Tests for services/strategies.ts: strategy API service functions.
 * Validates request construction, response mapping, and contract alignment.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import { api } from "@/services/api";
import {
  getStrategies,
  getStrategy,
  createStrategy,
  deleteStrategy,
  runBacktest,
  compareStrategies,
} from "@/services/strategies";

const mockedGet = vi.mocked(api.get);
const mockedPost = vi.mocked(api.post);
const mockedDelete = vi.mocked(api.delete);

describe("strategies service", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("getStrategies passes filter params", async () => {
    mockedGet.mockResolvedValueOnce({ items: [], total: 0 });
    await getStrategies({ symbol: "BTCUSDT", limit: 10 });
    expect(mockedGet).toHaveBeenCalledWith("/api/strategies", {
      params: { symbol: "BTCUSDT", limit: 10 },
    });
  });

  it("getStrategy fetches by id", async () => {
    const mockStrategy = { strategy_id: "s1", symbol: "BTCUSDT", timeframe: "4h" };
    mockedGet.mockResolvedValueOnce(mockStrategy);
    const result = await getStrategy("s1");
    expect(mockedGet).toHaveBeenCalledWith("/api/strategies/s1");
    expect(result.strategy_id).toBe("s1");
  });

  it("createStrategy posts payload", async () => {
    const payload = {
      dna: { signal_genes: [], logic_genes: {}, execution_genes: {}, risk_genes: {} },
      symbol: "BTCUSDT",
      timeframe: "4h",
      source: "manual",
    };
    mockedPost.mockResolvedValueOnce({ strategy_id: "new", ...payload });
    await createStrategy(payload);
    expect(mockedPost).toHaveBeenCalledWith("/api/strategies", { json: payload });
  });

  it("deleteStrategy calls correct endpoint", async () => {
    mockedDelete.mockResolvedValueOnce(null);
    await deleteStrategy("s1");
    expect(mockedDelete).toHaveBeenCalledWith("/api/strategies/s1");
  });

  it("runBacktest includes dataset_id and fee/slippage", async () => {
    const payload = {
      dna: {},
      symbol: "BTCUSDT",
      timeframe: "4h",
      dataset_id: "BTCUSDT_4h",
      init_cash: 100000,
      fee: 0.001,
      slippage: 0.0005,
    };
    const mockResult = {
      result_id: "r1",
      fee: 0.001,
      slippage: 0.0005,
      run_source: "lab",
      fitness: 0.68,
      qualified: true,
    };
    mockedPost.mockResolvedValueOnce(mockResult);
    const result = await runBacktest(payload);
    expect(mockedPost).toHaveBeenCalledWith("/api/strategies/backtest", { json: payload, timeout: 60000 });
    expect(result.fee).toBe(0.001);
    expect(result.run_source).toBe("lab");
  });

  it("compareStrategies posts strategy_ids", async () => {
    const payload = {
      strategy_ids: ["s1", "s2"],
      dataset_id: "BTCUSDT_4h",
      score_template: "profit_first",
    };
    mockedPost.mockResolvedValueOnce({ results: [] });
    await compareStrategies(payload);
    expect(mockedPost).toHaveBeenCalledWith("/api/strategies/compare", { json: payload });
  });
});
