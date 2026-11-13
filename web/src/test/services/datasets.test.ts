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
  getChartIndicators,
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

  // ── K 线请求路径段编码（kline-data-supply「K 线请求路径段编码」Requirement）──
  // 上方既有用例即 T1 合法值恒等回归锚（BTCUSDT/4h 逐字一致）；以下 T2-T5
  // 覆盖污染值编码形态与 URL 构造后的去向不变性。

  // T2：含 / 的污染值转义为 %2F——段内不产生路径分隔符（P3：timeframe 侧同形态）
  it.each([
    { symbol: "../evil", timeframe: "1h", expected: "/api/data/ohlcv/..%2Fevil/1h" },
    { symbol: "BTCUSDT", timeframe: "../evil", expected: "/api/data/ohlcv/BTCUSDT/..%2Fevil" },
  ])("getOhlcvBySymbol encodes path traversal in $symbol/$timeframe", async ({ symbol, timeframe, expected }) => {
    mockedGet.mockResolvedValueOnce({ dataset_id: "x", data: [] });
    await getOhlcvBySymbol(symbol, timeframe);
    expect(mockedGet).toHaveBeenCalledWith(expected, { params: undefined });
  });

  // T3：保留字符编码进段内——不注入 query、不截断 fragment
  //（P3 扩集：空格→%20、%→%25、反斜杠→%5C × symbol/timeframe 两字段）
  it.each([
    { symbol: "a?b=c", timeframe: "1h", expectedSymbol: "a%3Fb%3Dc", expectedTimeframe: "1h" },
    { symbol: "BTCUSDT", timeframe: "1h#f", expectedSymbol: "BTCUSDT", expectedTimeframe: "1h%23f" },
    { symbol: " ", timeframe: "1h", expectedSymbol: "%20", expectedTimeframe: "1h" },
    { symbol: "BTCUSDT", timeframe: " ", expectedSymbol: "BTCUSDT", expectedTimeframe: "%20" },
    { symbol: "%", timeframe: "1h", expectedSymbol: "%25", expectedTimeframe: "1h" },
    { symbol: "BTCUSDT", timeframe: "%", expectedSymbol: "BTCUSDT", expectedTimeframe: "%25" },
    { symbol: "\\", timeframe: "1h", expectedSymbol: "%5C", expectedTimeframe: "1h" },
    { symbol: "BTCUSDT", timeframe: "\\", expectedSymbol: "BTCUSDT", expectedTimeframe: "%5C" },
  ])("getOhlcvBySymbol encodes reserved chars in '$symbol'/'$timeframe'", async ({ symbol, timeframe, expectedSymbol, expectedTimeframe }) => {
    mockedGet.mockResolvedValueOnce({ dataset_id: "x", data: [] });
    await getOhlcvBySymbol(symbol, timeframe);
    expect(mockedGet).toHaveBeenCalledWith(
      `/api/data/ohlcv/${expectedSymbol}/${expectedTimeframe}`,
      { params: undefined },
    );
  });

  // T4：chart-indicators 同形编码——合法恒等 + 污染编码（? / # / 路径偏移形）
  it("getChartIndicators keeps legal URL identical", async () => {
    mockedGet.mockResolvedValueOnce({ ema: {}, boll: null, rsi: null, macd: null, kdj: null });
    await getChartIndicators("BTCUSDT", "4h", { limit: 200 });
    expect(mockedGet).toHaveBeenCalledWith("/api/data/chart-indicators/BTCUSDT/4h", {
      params: { limit: 200 },
    });
  });

  it.each([
    { symbol: "../evil", timeframe: "1h", expected: "/api/data/chart-indicators/..%2Fevil/1h" },
    { symbol: "a?b=c", timeframe: "1h#f", expected: "/api/data/chart-indicators/a%3Fb%3Dc/1h%23f" },
  ])("getChartIndicators encodes polluted segments in $symbol/$timeframe", async ({ symbol, timeframe, expected }) => {
    mockedGet.mockResolvedValueOnce({ ema: {}, boll: null, rsi: null, macd: null, kdj: null });
    await getChartIndicators(symbol, timeframe);
    expect(mockedGet).toHaveBeenCalledWith(expected, { params: undefined });
  });

  // T5：编码后 URL 经 WHATWG URL 构造——pathname 不发生 dot-segment 偏移、
  // search 仅含既有 params、hash 为空。用例值含 /（../evil）而非纯 '..'：
  // 纯 '..' 编码恒等、new URL 规范化后必偏移，属值域闸（traceSemantics）防线，
  // 不在编码层断言（design 卡 T5 注）
  it("encoded URL stays single-segment under new URL()", async () => {
    mockedGet.mockResolvedValueOnce({ dataset_id: "x", data: [] });
    await getOhlcvBySymbol("../evil", "1h", { limit: 100 });
    const url = mockedGet.mock.calls[0][0] as string;
    expect(url).toBe("/api/data/ohlcv/..%2Fevil/1h");

    const base = "http://localhost:5173";
    const full = new URL(base + url);
    // '..%2Fevil' 是单一非点段（%2F 不构成路径分隔符），不参与 dot-segment 规范化
    expect(full.pathname).toBe("/api/data/ohlcv/..%2Fevil/1h");
    expect(full.pathname.split("/")).toEqual(["", "api", "data", "ohlcv", "..%2Fevil", "1h"]);
    expect(full.search).toBe("");
    expect(full.hash).toBe("");

    // 带 params 构造（模拟 ky searchParams 拼接）：search 仅既有 params、无注入内容
    const withQuery = new URL(`${base}${url}?${new URLSearchParams({ limit: "100" })}`);
    expect(withQuery.search).toBe("?limit=100");
    expect(withQuery.hash).toBe("");
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
