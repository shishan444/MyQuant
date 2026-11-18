/**
 * 回放详情面板组件层测试（fe-replay-presentation P3 建网——纯函数
 * 8 用例之外的组件渲染/互达交互/挂载分派薄弱面）。
 *
 * mock 形制沿 RecordDetail.test.tsx：服务零外网（global fetch mock
 * 按 URL 分派 fills/signals）+KlineChart mock（jsdom 不渲染真图——
 * 断言传入 signalMarkers 六态）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ReplayDetailPanel } from "@/components/validationRecords/ReplayDetailPanel";
import { KlineChart } from "@/components/charts/KlineChart";

vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: vi.fn(() => null),
}));
const mockChart = vi.mocked(KlineChart);

const FILLS = [
  { bar_time: "2026-01-01T00:00:00Z", seq: 1, kind: "open",
    side: "long", price: 100, quantity: 2, fee: 0.1,
    realized_pnl: null, reason: null },
  { bar_time: "2026-01-02T00:00:00Z", seq: 2, kind: "add",
    side: "long", price: 101, quantity: 1, fee: 0.05,
    realized_pnl: null, reason: null },
  { bar_time: "2026-01-03T00:00:00Z", seq: 3, kind: "reduce",
    side: "long", price: 99, quantity: 1, fee: 0.05,
    realized_pnl: 1.2, reason: null },
  { bar_time: "2026-01-04T00:00:00Z", seq: 4, kind: "close",
    side: "long", price: 98, quantity: 2, fee: 0.1,
    realized_pnl: -3.4, reason: "reduce_terminated" },
];
const SIGNALS = [
  { bar_time: "2026-01-01T00:00:00Z", kind: "open", outcome: "filled",
    fill_seq: 1, reject_reason: null,
    gene_hits: [{ indicator: "RSI", field: "value",
                  condition: "lt", target: 30 }] },
  { bar_time: "2026-01-04T00:00:00Z", kind: "reduce", outcome: "filled",
    fill_seq: 4, reject_reason: null,
    gene_hits: [{ indicator: "EMA", condition: "crossunder",
                  target: "ema_50" }] },
];

const RECORD = (taskId?: string) => ({
  record_id: "rec_test", level: "replay",
  result: taskId
    ? { replay: { task_id: taskId, status: "completed",
                  total_trades: 4, total_pnl: -2.2 } }
    : {},
});

function renderPanel(record: ReturnType<typeof RECORD>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(<ReplayDetailPanel record={record} />, { wrapper });
}

describe("ReplayDetailPanel 组件渲染与互达（P3 建网）", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    mockChart.mock.calls.length = 0;
  });
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch(fills: unknown[], signals: unknown[]) {
    fetchMock.mockImplementation((url: string) => {
      const body = url.includes("/fills")
        ? { fills }
        : { signals };
      return Promise.resolve({
        ok: true, json: () => Promise.resolve(body),
      } as Response);
    });
  }

  it("数据态：流水表逐笔渲染+六态标记传图表（开/加/减/平全链）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-1"));
    await waitFor(() => {
      expect(
        screen.getByTestId("replay-position-flow")).toBeInTheDocument();
    });
    // 四行逐笔：动作字义（六态派生）+减仓终止触发原因文案
    const rows = FILLS.map((_, i) =>
      screen.getByTestId(`replay-flow-row-${i}`));
    expect(rows).toHaveLength(4);
    expect(rows[0].textContent).toContain("开多");
    expect(rows[1].textContent).toContain("加仓");
    expect(rows[2].textContent).toContain("减仓");
    expect(rows[3].textContent).toContain("平多");
    expect(rows[3].textContent).toContain("减仓终止");
    // 六态标记经 KlineChart signalMarkers 传入（点位锚定）
    await waitFor(() => {
      const calls = mockChart.mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      // React 函数组件调用传 (props, context) 两参——props 在首参
      const props = calls[calls.length - 1][0] as {
        signalMarkers?: Array<{ text: string; price: number }>;
      };
      const markers = props.signalMarkers ?? [];
      expect(markers.map((m) => m.text)).toEqual(
        ["开多", "加仓", "减仓", "平多"]);
      expect(markers[0].price).toBe(100);
    });
  });

  it("互达交互：点击流水行展开基因描述，再点收起", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-2"));
    const row = await screen.findByTestId("replay-flow-row-0");
    // 初始不展开
    expect(screen.queryByText(/RSI\.value lt 30/)).toBeNull();
    fireEvent.click(row);
    expect(screen.getByText(/基因：RSI\.value lt 30/)).toBeInTheDocument();
    fireEvent.click(row);
    expect(screen.queryByText(/基因：RSI\.value lt 30/)).toBeNull();
  });

  it("旧形制空态：result 无 replay.task_id → 不可复核提示", () => {
    renderPanel(RECORD(undefined));
    expect(screen.getByText(/缺 task_id 引用/)).toBeInTheDocument();
  });

  it("零成交空态：fills 空 → 如实空态非报错", async () => {
    stubFetch([], SIGNALS);
    renderPanel(RECORD("task-3"));
    await waitFor(() => {
      expect(screen.getByText(/无成交/)).toBeInTheDocument();
    });
  });
});
