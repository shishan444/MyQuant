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

  it("CARD-05 两半源未建同时渲染（reason 单字段拼接）", async () => {
    vi.mocked(wb.getPendingEvents).mockResolvedValue({
      events: [], degraded: 0,
      availability: {
        qualify_pending: "unavailable",
        trigger_deviation: "unavailable",
        reason: "C5 判定器源未建（旧库行无 qualification_progress）；trigger 回看源未建（终态报告无 qualification 键——C5 前旧报告）",
      },
    } as never);
    setup();
    // 两半 unavailable 渲染块并存（区分「无事件」与「源未建」）
    const qualifyBlock = await screen.findByText(/达标事件源未建/);
    expect(screen.getByText(/触发差异事件源未建/)).toBeInTheDocument();
    // qualify 块内嵌完整拼接 reason——两半说明同现一处（单字段非分源；
    // JSX 插值产独立文本节点，regex 匹配器不可跨段——textContent 断言）
    expect(qualifyBlock.textContent).toContain("旧库行无 qualification_progress");
    expect(qualifyBlock.textContent).toContain("trigger 回看源未建");
    expect(qualifyBlock.textContent).toContain("；");
  });

  it("CARD-05 仅 trigger 半边源未建（qualify 可用不渲染 qualify 块）", async () => {
    vi.mocked(wb.getPendingEvents).mockResolvedValue({
      events: [], degraded: 0,
      availability: {
        qualify_pending: null,
        trigger_deviation: "unavailable",
        reason: "trigger 回看源未建（终态报告无 qualification 键——C5 前旧报告）",
      },
    } as never);
    setup();
    expect(await screen.findByText(/触发差异事件源未建/)).toBeInTheDocument();
    expect(screen.queryByText(/达标事件源未建/)).not.toBeInTheDocument();
  });

  it("CARD-03 验证记录源错误态呈现（B2-3 四态补全）", async () => {
    vi.mocked(wb.getValidationRecords).mockRejectedValue(new Error("net") as never);
    setup();
    expect(await screen.findByText(/验证记录源暂不可达/)).toBeInTheDocument();
  });

  it("CARD-04 B4 假状态徽章已删（api/mcp 独立文本节点不再渲染）", async () => {
    setup();
    await screen.findByText(/最近验证结论/);  // 页面就绪后再负向断言
    // exact 匹配只命中独立文本节点——B4 残留说明串「api/mcp 进程」不受影响
    expect(screen.queryByText("api", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("mcp", { exact: true })).not.toBeInTheDocument();
  });

  it("CARD-07 truncated 页满披露渲染（与 degraded/availability 并存）", async () => {
    vi.mocked(wb.getPendingEvents).mockResolvedValue({
      events: [], degraded: 1,
      scanned_tasks: 1000, truncated: true,
      availability: { qualify_pending: null, trigger_deviation: null, reason: null },
    } as never);
    setup();
    // truncated 提示块呈现（含扫描数披露）
    expect(await screen.findByText(/终态任务扫描达上限/)).toBeInTheDocument();
    // 独立字段独立渲染块——与 degraded 披露并存不互斥
    expect(screen.getByText(/1 个事件源扫描失败/)).toBeInTheDocument();
  });

  it("CARD-07 truncated=false 不渲染提示块", async () => {
    vi.mocked(wb.getPendingEvents).mockResolvedValue({
      events: [], degraded: 0,
      scanned_tasks: 5, truncated: false,
      availability: { qualify_pending: null, trigger_deviation: null, reason: null },
    } as never);
    setup();
    await screen.findByText(/暂无待裁决事件/);
    expect(screen.queryByText(/终态任务扫描达上限/)).not.toBeInTheDocument();
  });
});
