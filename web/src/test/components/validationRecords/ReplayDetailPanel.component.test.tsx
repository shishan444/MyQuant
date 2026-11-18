/**
 * 回放详情面板组件层测试（fe-replay-presentation P3 建网+CARD-01/03
 * 验收网）。
 *
 * mock 面：KlineChart mock（jsdom 不渲染真图——断言 props 链：
 * data 主体/signalMarkers 六态/selectedMarkerTime/onMarkerClick）+
 * getOhlcvBySymbol 服务层 mock（CARD-01 取数单源）+global fetch
 * stubGlobal 按 URL 分派 fills/signals。
 * 踩坑实录：React 函数组件 mock 调用传 (props, context) 两参——
 * props 在 calls[-1][0]。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ReplayDetailPanel } from "@/components/validationRecords/ReplayDetailPanel";
import { epochOf } from "@/components/validationRecords/fullRangeData";
import { KlineChart } from "@/components/charts/KlineChart";
import { getOhlcvBySymbol } from "@/services/datasets";

vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: vi.fn(() => null),
}));
vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(),
}));
const mockChart = vi.mocked(KlineChart);
const mockOhlcv = vi.mocked(getOhlcvBySymbol);

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
  // P3-CARD-02 键形制：bar_time=决策锚（前一根）、exec_bar_time=
  // 执行 bar（与 fill.bar_time 同轴——geneDescription join 键）
  { bar_time: "2025-12-31T20:00:00Z",
    exec_bar_time: "2026-01-01T00:00:00Z",
    kind: "open", outcome: "filled",
    fill_seq: 1, reject_reason: null,
    gene_hits: [{ indicator: "RSI", field: "value",
                  condition: "lt", target: 30 }] },
];
const OHLCV = [
  { timestamp: "2026-01-01T00:00:00Z", open: 99, high: 101,
    low: 98, close: 100 },
  { timestamp: "2026-01-02T00:00:00Z", open: 100, high: 102,
    low: 99, close: 101 },
];

const RECORD = (taskId?: string, params?: Record<string, unknown>) => ({
  record_id: "rec_test", level: "replay",
  params: params === undefined
    ? { symbol: "BTCUSDT", timeframe: "4h",
        start: "2026-01-01", end: "2026-01-31" }
    : params,
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

/** KlineChart mock 最新 props（calls[-1][0]——两参坑在案） */
function lastChartProps() {
  const calls = mockChart.mock.calls;
  return calls[calls.length - 1][0] as {
    data?: unknown[];
    signalMarkers?: Array<{ text: string; price: number }>;
    selectedMarkerTime?: number | null;
    onMarkerClick?: (t: number) => void;
  };
}

describe("ReplayDetailPanel 组件渲染与互达（P3 建网+CARD-01/03 验收）", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    mockChart.mock.calls.length = 0;
    mockOhlcv.mockReset();
    mockOhlcv.mockResolvedValue({ data: OHLCV } as never);
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch(fills: unknown[], signals: unknown[]) {
    fetchMock.mockImplementation((url: string) => {
      const body = url.includes("/fills") ? { fills } : { signals };
      return Promise.resolve({
        ok: true, json: () => Promise.resolve(body),
      } as Response);
    });
  }

  it("数据态：主体非空+流水表逐笔+六态标记传图表（CARD-01 ①）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-1"));
    await waitFor(() => {
      // K 线主体挂载：data 非空（服务层真值直传）
      expect(mockChart.mock.calls.length).toBeGreaterThan(0);
      expect((lastChartProps().data ?? []).length).toBe(2);
    });
    const markers = lastChartProps().signalMarkers ?? [];
    expect(markers.map((m) => m.text)).toEqual(
      ["开多", "加仓", "减仓", "平多"]);
    expect(markers[0].price).toBe(100);
    // 流水表逐笔（六态字义+减仓终止文案）
    const rows = FILLS.map((_, i) =>
      screen.getByTestId(`replay-flow-row-${i}`));
    expect(rows).toHaveLength(4);
    expect(rows[3].textContent).toContain("平多");
    expect(rows[3].textContent).toContain("减仓终止");
  });

  it("互达行→图：点击流水行 selectedMarkerTime=该 bar epoch（CARD-03 ②a）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-2"));
    await screen.findByTestId("replay-flow-row-0");
    fireEvent.click(screen.getByTestId("replay-flow-row-0"));
    await waitFor(() => {
      // epoch 域同源对齐（epochOf 与 KlineChart toTime 单源）
      expect(lastChartProps().selectedMarkerTime).toBe(
        epochOf(FILLS[0].bar_time));
    });
    // 基因描述随选中展开（互达行侧伴生）
    expect(screen.getByText(/基因：RSI\.value lt 30/)).toBeInTheDocument();
  });

  it("互达图→行：onMarkerClick 定位该 bar 首笔行滚动高亮（CARD-03 ②b）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-3"));
    await waitFor(() => {
      expect(mockChart.mock.calls.length).toBeGreaterThan(0);
    });
    const click = lastChartProps().onMarkerClick;
    expect(click).toBeTypeOf("function");
    // 模拟图上点击第一根成交 bar（epoch 域）
    click!(epochOf(FILLS[0].bar_time)!);
    await waitFor(() => {
      const row = screen.getByTestId("replay-flow-row-0");
      expect(row.className).toContain("bg-surface-active");
    });
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    // 基因描述随行选中展开
    expect(screen.getByText(/基因：RSI\.value lt 30/)).toBeInTheDocument();
  });

  it("零成交主体仍在：fills 空→空态文案+K 线挂载标记空（CARD-01 ③）", async () => {
    stubFetch([], SIGNALS);
    renderPanel(RECORD("task-4"));
    await screen.findByText(/无成交/);
    await waitFor(() => {
      expect(mockChart.mock.calls.length).toBeGreaterThan(0);
    });
    expect((lastChartProps().data ?? []).length).toBe(2);
    expect(lastChartProps().signalMarkers).toEqual([]);
  });

  it("ohlcv 不可达降级：文案如实+流水表不受影响（CARD-01 ④）", async () => {
    mockOhlcv.mockRejectedValue(new Error("api down") as never);
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-5"));
    await screen.findByText(/K 线数据源暂不可达/);
    // 成交明细与标记通道不受影响：流水表照常渲染
    expect(screen.getByTestId("replay-position-flow")).toBeInTheDocument();
    expect(screen.getByTestId("replay-flow-row-0")).toBeInTheDocument();
  });

  it("params 缺四键：主体不可得文案如实（CARD-01 降级面）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-6", {}));
    await screen.findByText(/K 线主体不可得/);
  });

  it("旧形制空态：result 无 replay.task_id → 不可复核提示", () => {
    renderPanel(RECORD(undefined));
    expect(screen.getByText(/缺 task_id 引用/)).toBeInTheDocument();
  });

  it("tid 形制闸拒路径注入（CARD-06 F-P3-2）：../ 语义串 → 引用"
      + "不可用降级且零请求", async () => {
    renderPanel(RECORD("../etc/passwd"));
    expect(screen.getByText(/形制不可用/)).toBeInTheDocument();
    // 闸后零请求（useQuery enabled !!tid——fetch 不触达）
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockOhlcv).not.toHaveBeenCalled();
  });

  it("tid 形制闸白名单边界：64 位合法形制直通（不误拒）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("a".repeat(64)));
    await screen.findByTestId("replay-position-flow");
  });
});
