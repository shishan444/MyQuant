/**
 * Tests for services/datasets.ts: dataset API service functions.
 * Validates request construction and response mapping.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

import { api } from "@/services/api";
import {
  getDatasets,
  getOhlcv,
  getOhlcvBySymbol,
  getAvailableSources,
  deleteDataset,
} from "@/services/datasets";

const mockedGet = vi.mocked(api.get);
const mockedDelete = vi.mocked(api.delete);

describe("datasets service", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("getDatasets returns items (matching Python 'items' field)", async () => {
    const mockResponse = {
      items: [
        { dataset_id: "d1", symbol: "BTCUSDT", interval: "4h", row_count: 100 },
      ],
      total: 1,
    };
    mockedGet.mockResolvedValueOnce(mockResponse);

    const result = await getDatasets();
    expect(result.items).toHaveLength(1);
    expect(result.total).toBe(1);
  });

  it("getDatasets passes filter params", async () => {
    mockedGet.mockResolvedValueOnce({ items: [], total: 0 });
    await getDatasets({ symbol: "BTCUSDT", interval: "4h" });
    expect(mockedGet).toHaveBeenCalledWith("/api/data/datasets", {
      params: { symbol: "BTCUSDT", interval: "4h" },
    });
  });

  it("getOhlcv fetches by dataset id", async () => {
    const mockOhlcv = {
      dataset_id: "BTCUSDT_4h",
      data: [{ timestamp: "2024-01-01", open: 100, high: 110, low: 95, close: 105, volume: 1000 }],
    };
    mockedGet.mockResolvedValueOnce(mockOhlcv);
    const result = await getOhlcv("BTCUSDT_4h");
    expect(mockedGet).toHaveBeenCalledWith("/api/data/datasets/BTCUSDT_4h/ohlcv", { params: undefined });
    expect(result.dataset_id).toBe("BTCUSDT_4h");
    expect(result.data).toHaveLength(1);
  });

  it("getOhlcvBySymbol constructs correct URL", async () => {
    mockedGet.mockResolvedValueOnce({ dataset_id: "BTCUSDT_4h", data: [] });
    await getOhlcvBySymbol("BTCUSDT", "4h", { limit: 100 });
    expect(mockedGet).toHaveBeenCalledWith("/api/data/ohlcv/BTCUSDT/4h", { params: { limit: 100 } });
  });

  it("getAvailableSources calls correct endpoint", async () => {
    mockedGet.mockResolvedValueOnce({ sources: [{ symbol: "BTCUSDT", timeframe: "4h" }] });
    const result = await getAvailableSources();
    expect(mockedGet).toHaveBeenCalledWith("/api/data/available-sources");
    expect(result.sources).toHaveLength(1);
  });

  it("deleteDataset calls correct endpoint", async () => {
    mockedDelete.mockResolvedValueOnce(null);
    await deleteDataset("d1");
    expect(mockedDelete).toHaveBeenCalledWith("/api/data/datasets/d1");
  });
});
