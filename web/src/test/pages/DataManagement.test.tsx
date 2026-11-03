/**
 * 09 数据管理页完整性区特征测试。fe-asset-data-domain 4.3 后锚=双币
 * 并列月矩阵（BTCUSDT/ETHUSDT 两请求）+保鲜 12 路满格（FIXED 键全集+
 * 未扫占位）；原 CARD-02 单币双态提交用例随输入框退役改写。
 * 服务层全 mock（零外网红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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


describe("DataManagement 双币月矩阵（fe-asset-data-domain 4.3）", () => {
  it("双币并列：BTCUSDT/ETHUSDT 两请求+双矩阵在场，单币输入框退役", async () => {
    setup();
    await waitFor(() => {
      expect(dwb.getMonthlyMatrix).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "BTCUSDT", months_back: 6 }));
      expect(dwb.getMonthlyMatrix).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "ETHUSDT", months_back: 6 }));
    });
    // 双矩阵 testid 在场（integrity-matrix-{symbol}）
    expect(await screen.findByTestId("integrity-matrix-BTCUSDT")).toBeInTheDocument();
    expect(await screen.findByTestId("integrity-matrix-ETHUSDT")).toBeInTheDocument();
    // 单币输入框退役（原 F2 双态提交机制随消费消亡）
    expect(screen.queryByLabelText(/矩阵 symbol/)).not.toBeInTheDocument();
  });

  it("保鲜 12 路满格：FIXED 键全集在场（含未扫占位——键集不全恒满格）", async () => {
    setup();
    const table = await screen.findByTestId("freshness-table-fixed");
    // 12 路键全集逐键在场
    for (const key of ["BTCUSDT_1m", "BTCUSDT_4h", "ETHUSDT_1h", "ETHUSDT_30m"]) {
      expect(table).toHaveTextContent(key);
    }
    expect(screen.getByText(/12 路 = 2 币 × 6 档/)).toBeInTheDocument();
  });
});
