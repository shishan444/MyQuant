/**
 * 09 数据管理页 F2 双态提交特征测试（data-management-revamp CARD-02）：
 * symbol 输入 display/committed 双 state——中间态不触发请求、失焦/回车
 * 提交、空值不提交保留旧 committed。服务层全 mock（零外网红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/services/datasets", () => ({
  getDatasets: vi.fn(),
  importCsv: vi.fn(),
  importCsvBatch: vi.fn(),
  deleteDataset: vi.fn(),
  getDataset: vi.fn(),
  getOhlcv: vi.fn(),
  getPreview: vi.fn(),
  getAvailableSources: vi.fn(),
}));

vi.mock("@/services/dataWorkbench", () => ({
  getMonthlyMatrix: vi.fn(),
  getFreshnessStatus: vi.fn(),
}));

import { DataManagement } from "@/pages/DataManagement";
import * as ds from "@/services/datasets";
import * as dwb from "@/services/dataWorkbench";

const DATASET = {
  id: 1, symbol: "BTCUSDT", interval: "4h", row_count: 180,
  size_bytes: 10240, quality_status: "complete",
  first_ts: "2026-09-01 00:00:00", last_ts: "2026-09-30 20:00:00",
  created_at: "2026-10-01T00:00:00Z",
};

const MATRIX = {
  symbol: "BTCUSDT", generated_at: "2026-11-01T00:00:00Z", months_back: 6,
  short_period: {
    timeframes: ["4h"],
    matrix: [
      { month: "2026-10", cells: [{ timeframe: "4h", status: "green", bars_actual: 180, bars_expected: 180, gap_count: 0, first_gap: null }] },
    ],
  },
  long_period: [],
};

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DataManagement />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(ds.getDatasets).mockResolvedValue({ items: [DATASET], total: 1 } as never);
  vi.mocked(dwb.getMonthlyMatrix).mockResolvedValue(MATRIX as never);
  vi.mocked(dwb.getFreshnessStatus).mockResolvedValue({
    kline: { last_run: null, keys: {} }, derivs: { last_run: null, symbols: {} },
  } as never);
});

async function settled() {
  await screen.findByTestId("integrity-matrix");
  return vi.mocked(dwb.getMonthlyMatrix).mock.calls.length;
}

describe("DataManagement F2 双态提交（CARD-02）", () => {
  it("输入中间态不触发 monthly-matrix 请求（display 态不进 queryKey）", async () => {
    setup();
    const before = await settled();
    const input = screen.getByLabelText(/矩阵 symbol/);
    fireEvent.change(input, { target: { value: "ETH" } });
    // display 层即时转换呈现，但不提交——请求次数不变
    expect(input).toHaveValue("ETH");
    expect(vi.mocked(dwb.getMonthlyMatrix).mock.calls.length).toBe(before);
  });

  it("回车提交：committed 更新并以新 symbol 发起请求", async () => {
    setup();
    await settled();
    const input = screen.getByLabelText(/矩阵 symbol/);
    fireEvent.change(input, { target: { value: "eth" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(dwb.getMonthlyMatrix).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "ETH", months_back: 6 }));
    });
  });

  it("失焦提交：blur 后以新 symbol 发起请求", async () => {
    setup();
    await settled();
    const input = screen.getByLabelText(/矩阵 symbol/);
    fireEvent.change(input, { target: { value: "SOL" } });
    fireEvent.blur(input);
    await waitFor(() => {
      expect(dwb.getMonthlyMatrix).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "SOL", months_back: 6 }));
    });
  });

  it("空值不提交：清空后失焦保留旧 committed（不发起空请求）", async () => {
    setup();
    const before = await settled();
    const input = screen.getByLabelText(/矩阵 symbol/);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    fireEvent.keyDown(input, { key: "Enter" });
    // 空值两次提交路径都不触发——次数与提交前一致
    expect(vi.mocked(dwb.getMonthlyMatrix).mock.calls.length).toBe(before);
  });
});
