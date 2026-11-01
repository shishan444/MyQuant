/**
 * 09 完整性月矩阵+保鲜表组件特征测试（data-management-revamp 前端组，
 * 审查阶段补网：原零单测）。
 * 三态格渲染/缺口申报清单（REJ-04 申报制）/长周期整体行/503 态/空态
* /错误态——服务层全 mock，零真实请求（测试外网硬红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/services/dataWorkbench", () => ({
  getMonthlyMatrix: vi.fn(),
  getFreshnessStatus: vi.fn(),
}));

import { IntegrityMatrix, FreshnessTable } from "@/components/data/IntegrityPanels";
import * as dwb from "@/services/dataWorkbench";

function setup(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

const MATRIX = {
  symbol: "BTCUSDT", generated_at: "2026-11-01T00:00:00Z", months_back: 3,
  short_period: {
    timeframes: ["4h"],
    matrix: [
      { month: "2026-09", cells: [{ timeframe: "4h", status: "green", bars_actual: 180, bars_expected: 180, gap_count: 0, first_gap: null }] },
      { month: "2026-10", cells: [{ timeframe: "4h", status: "yellow", bars_actual: 170, bars_expected: 180, gap_count: 1, first_gap: { start: "2026-10-05 00:00:00", end: "2026-10-07 00:00:00" } }] },
      { month: "2026-11", cells: [{ timeframe: "4h", status: "gray", bars_actual: 0, bars_expected: 0, gap_count: 0, first_gap: null }] },
    ],
  },
  long_period: [
    { timeframe: "1d", status: "yellow", coverage: { first_bar: "2026-05-01 00:00:00", last_bar: "2026-08-28 00:00:00", bars: 120 }, gap_count: 2, gaps: [{ start: "2026-06-20 00:00:00", end: "2026-06-23 00:00:00" }] },
  ],
};

const FRESH = {
  kline: {
    last_run: "2026-11-01T08:00:00+00:00",
    keys: { "BTCUSDT_4h": "fresh", "BTCUSDT_1h": "stale" } as Record<string, string>,
  },
  derivs: {
    last_run: null,
    symbols: { BTCUSDT: { funding: "fresh", oi: "missing" } },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dwb.getMonthlyMatrix).mockResolvedValue(MATRIX as never);
  vi.mocked(dwb.getFreshnessStatus).mockResolvedValue(FRESH as never);
});

describe("IntegrityMatrix（09-B2r 月矩阵）", () => {
  it("三态格渲染：green/yellow/gray 月格各以其状态标签在场", async () => {
    setup(<IntegrityMatrix symbol="BTCUSDT" monthsBack={3} />);
    expect(await screen.findByLabelText("2026-09-4h-green")).toBeInTheDocument();
    expect(screen.getByLabelText("2026-10-4h-yellow")).toBeInTheDocument();
    expect(screen.getByLabelText("2026-11-4h-gray")).toBeInTheDocument();
  });

  it("缺口申报清单：黄色格计数、零 bar 灰格不计数（数据缺失非异常）", async () => {
    setup(<IntegrityMatrix symbol="BTCUSDT" monthsBack={3} />);
    // 1 yellow（有数据）+1 gray（零 bar）→ 申报只计 1
    expect(await screen.findByText(/检出 1 个非完整月单元/)).toBeInTheDocument();
    // REJ-04：申报制文案点明修复动作不在此页提供
    expect(screen.getByText(/修复动作不在此页提供/)).toBeInTheDocument();
  });

  it("全 green 月矩阵无申报清单块", async () => {
    const green = { ...MATRIX, short_period: { timeframes: ["4h"], matrix: [MATRIX.short_period.matrix[0]] } };
    vi.mocked(dwb.getMonthlyMatrix).mockResolvedValue(green as never);
    setup(<IntegrityMatrix symbol="BTCUSDT" monthsBack={3} />);
    await screen.findByTestId("integrity-matrix");
    expect(screen.queryByText(/非完整月单元/)).toBeNull();
  });

  it("长周期整体行：档名+根数+缺口数呈现", async () => {
    setup(<IntegrityMatrix symbol="BTCUSDT" monthsBack={3} />);
    const row = await screen.findByTestId("long-1d");
    expect(row).toBeInTheDocument();
    expect(row.textContent).toContain("1d");
    expect(row.textContent).toContain("120 根");
    expect(row.textContent).toContain("缺口 2 处");
  });

  it("端点错误态如实呈现（不伪装空矩阵）", async () => {
    vi.mocked(dwb.getMonthlyMatrix).mockRejectedValue(new Error("network") as never);
    setup(<IntegrityMatrix symbol="BTCUSDT" monthsBack={3} />);
    expect(await screen.findByText(/完整性数据暂不可达/)).toBeInTheDocument();
  });

  it("装载中态有过渡文案", async () => {
    vi.mocked(dwb.getMonthlyMatrix).mockImplementation(() => new Promise(() => {}) as never);
    setup(<IntegrityMatrix symbol="BTCUSDT" monthsBack={3} />);
    expect(screen.getByText(/月矩阵装载中/)).toBeInTheDocument();
  });
});

describe("FreshnessTable（09-B6r 保鲜表）", () => {
  it("正常态：kline 键六态徽章+last_run+derivs 徽章", async () => {
    setup(<FreshnessTable />);
    // 键与六态分属父子节点——容器级 textContent 断言
    const box = await screen.findByTestId("freshness-table");
    expect(box.textContent).toContain("BTCUSDT_4h·fresh");
    expect(box.textContent).toContain("BTCUSDT_1h·stale");
    expect(box.textContent).toContain("上轮检查 2026-11-01 08:00:00");
    expect(box.textContent).toContain("BTCUSDT·funding/oi");
  });

  it("503 态：kline/derivs 双 null 如实呈现守护器未装配", async () => {
    vi.mocked(dwb.getFreshnessStatus).mockResolvedValue({ kline: null, derivs: null } as never);
    setup(<FreshnessTable />);
    expect(await screen.findByText(/保鲜守护器未装配/)).toBeInTheDocument();
  });

  it("错误态（isError）同走未装配文案", async () => {
    vi.mocked(dwb.getFreshnessStatus).mockRejectedValue(new Error("503") as never);
    setup(<FreshnessTable />);
    expect(await screen.findByText(/保鲜守护器未装配/)).toBeInTheDocument();
  });

  it("空态：kline.keys 空记录「暂无检查记录」", async () => {
    const empty = { ...FRESH, kline: { last_run: null, keys: {} } };
    vi.mocked(dwb.getFreshnessStatus).mockResolvedValue(empty as never);
    setup(<FreshnessTable />);
    expect(await screen.findByText(/K 线路暂无检查记录/)).toBeInTheDocument();
    expect(screen.getByText(/上轮检查 未运行/)).toBeInTheDocument();
  });
});
