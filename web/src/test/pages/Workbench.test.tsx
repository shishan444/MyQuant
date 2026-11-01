/**
 * 01 观察工作台单测（workbench-observation 前端组）。
 * B1 事件区四态+三类事件行+导航；B2-3 验证结论概要；降级披露。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/services/workbench", () => ({
  getPendingEvents: vi.fn(),
  getValidationRecords: vi.fn(),
}));

import { Workbench } from "@/pages/Workbench";
import * as wb from "@/services/workbench";

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Workbench />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const EVENTS = {
  events: [
    { type: "union_exhausted", payload: { hypothesis_id: "fr_1", symbol: "BTCUSDT", timeframe: "4h", remaining_bars: 0 } },
    { type: "qualify_pending", payload: { task_id: "t1", strategy_id: "st_1", first_met_at: null } },
    { type: "trigger_deviation", payload: { task_id: "t2", strategy_id: "st_2", deviation_ratio: 0.25 } },
  ],
  degraded: 2,
  availability: { qualify_pending: null, trigger_deviation: null, reason: null },
};

const RECORDS = {
  records: [
    { record_id: "r1", hypothesis_id: "fr_1", strategy_id: null, level: "isolated_backtest",
      segmentation: "", verdict: "pass", range_hash: "h", pollution_cause: null,
      created_at: "2026-11-01T00:00:00+00:00", params: {}, result: {} },
  ],
  total: 1, offset: 0, limit: 8, has_more: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(wb.getPendingEvents).mockResolvedValue(EVENTS as never);
  vi.mocked(wb.getValidationRecords).mockResolvedValue(RECORDS as never);
});

describe("Workbench（01 观察工作台）", () => {
  it("B1 三类事件行渲染+金色置顶样式", async () => {
    setup();
    expect(await screen.findByTestId("event-union_exhausted")).toBeInTheDocument();
    expect(screen.getByTestId("event-qualify_pending")).toBeInTheDocument();
    expect(screen.getByTestId("event-trigger_deviation")).toBeInTheDocument();
    // 金色（并集耗尽行含注意力金描边类）
    expect(screen.getByTestId("event-union_exhausted").className).toContain("attention-gold");
  });

  it("事件区四态：空态文案与降级披露", async () => {
    vi.mocked(wb.getPendingEvents).mockResolvedValue({
      events: [], degraded: 1,
      availability: { qualify_pending: "unavailable", trigger_deviation: null, reason: "C5 源未建" },
    } as never);
    setup();
    expect(await screen.findByText("暂无待裁决事件")).toBeInTheDocument();
    expect(screen.getByText(/1 个事件源扫描失败/)).toBeInTheDocument();
    expect(screen.getByText(/达标事件源未建/)).toBeInTheDocument();
  });

  it("事件源错误态呈现", async () => {
    vi.mocked(wb.getPendingEvents).mockRejectedValue(new Error("net") as never);
    setup();
    expect(await screen.findByText(/事件源暂不可达/)).toBeInTheDocument();
  });

  it("B2-3 最近验证结论：verdict 色点+概要默认", async () => {
    setup();
    expect(await screen.findAllByText(/fr_1/)).not.toHaveLength(0);  // 事件行+记录行两处
    expect(screen.getByText(/● pass/).className).toContain("text-profit");
  });

  it("B2-3 空态", async () => {
    vi.mocked(wb.getValidationRecords).mockResolvedValue({
      records: [], total: 0, offset: 0, limit: 8, has_more: false } as never);
    setup();
    expect(await screen.findByText("暂无验证记录")).toBeInTheDocument();
  });
});
