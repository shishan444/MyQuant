/**
 * 回放详情面板组件层测试（fe-replay-presentation P3 建网+CARD-01/03
 * 验收网）。
 *
 * mock 面：KlineChart mock（jsdom 不渲染真图——断言 props 链：
 * data 主体/signalMarkers 六态/selectedMarkerTime/onMarkerClick）+
 * 服务层 mock（getOhlcvBySymbol 与 getTradingFills/getTradingSignals
 * ——CARD-01 取数单源收敛后的注入点）；失败形（422/5xx/网络错，经
 * ApiError 仿真）与分页形（offset/has_more 两页）补齐（fix-replay-
 * presentation-pipeline——mock 契约边界教训：mock 只含 ok 形曾掩盖
 * 恒 422 缺陷）。
 * 踩坑实录：React 函数组件 mock 调用传 (props, context) 两参——
 * props 在 calls[-1][0]。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ReplayDetailPanel } from "@/components/validationRecords/ReplayDetailPanel";
import { epochOf } from "@/components/validationRecords/fullRangeData";
import { KlineChart } from "@/components/charts/KlineChart";
import { getOhlcvBySymbol } from "@/services/datasets";
import {
  getTradingEquity, getTradingFills, getTradingSignals,
} from "@/services/trading";
import { ApiError } from "@/services/api";

vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: vi.fn(() => null),
}));
vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(),
}));
vi.mock("@/services/trading", () => ({
  getTradingFills: vi.fn(),
  getTradingSignals: vi.fn(),
  getTradingEquity: vi.fn(),
}));
const mockChart = vi.mocked(KlineChart);
const mockOhlcv = vi.mocked(getOhlcvBySymbol);
const mockFills = vi.mocked(getTradingFills);
const mockSignals = vi.mocked(getTradingSignals);
const mockEquity = vi.mocked(getTradingEquity);

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
    // C4 D7：condition dict 形制（后端 dna.py 权威——{"type","threshold"}）
    gene_hits: [{ indicator: "RSI", field: "value",
                  condition: { type: "lt", threshold: 30 } }] },
];
const OHLCV = [
  { timestamp: "2026-01-01T00:00:00Z", open: 99, high: 101,
    low: 98, close: 100 },
  { timestamp: "2026-01-02T00:00:00Z", open: 100, high: 102,
    low: 99, close: 101 },
];

const RECORD = (taskId?: string, params?: Record<string, unknown>) => ({
  record_id: "rec_test", level: "replay",
  // statement 可空：replay 恒 adhoc 无假设关联显「—」（D6 空态）；
  // 放宽 string|null 供真值呈现用例覆盖构造
  statement: null as string | null,
  params: params === undefined
    ? { symbol: "BTCUSDT", timeframe: "4h",
        start: "2026-01-01", end: "2026-01-31",
        fee: 0.001, slippage: 0.0005, init_cash: 100000, leverage: 1 }
    : params,
  result: taskId
    ? { replay: { task_id: taskId, status: "completed",
                  total_trades: 4, total_pnl: -2.2,
                  final_equity: 99880 } }
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
  beforeEach(() => {
    mockFills.mockReset();
    mockSignals.mockReset();
    mockChart.mock.calls.length = 0;
    mockOhlcv.mockReset();
    mockOhlcv.mockResolvedValue({ data: OHLCV } as never);
    mockEquity.mockReset();
    mockEquity.mockResolvedValue({ snapshots: [],
                                   has_more: false } as never);
    Element.prototype.scrollIntoView = vi.fn();
  });

  function stubFetch(fills: unknown[], signals: unknown[]) {
    mockFills.mockResolvedValue({ fills, has_more: false } as never);
    mockSignals.mockResolvedValue({ signals, has_more: false } as never);
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
    expect(screen.getByText(/基因：RSI\.value < 30/)).toBeInTheDocument();
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
    expect(screen.getByText(/基因：RSI\.value < 30/)).toBeInTheDocument();
  });

  it("行选中 toggle：二次点击同一行取消选中（CARD-01 流水表迁移行"
      + " 交互契约）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("task-toggle"));
    const row = await screen.findByTestId("replay-flow-row-0");
    fireEvent.click(row);
    await waitFor(() => {
      expect(row.className).toContain("bg-surface-active");
    });
    fireEvent.click(row);
    await waitFor(() => {
      expect(row.className).not.toContain("bg-surface-active");
    });
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
    // 闸后零请求（useQuery enabled !!tid——服务层不触达）
    expect(mockFills).not.toHaveBeenCalled();
    expect(mockSignals).not.toHaveBeenCalled();
    expect(mockOhlcv).not.toHaveBeenCalled();
  });

  it("tid 形制闸白名单边界：64 位合法形制直通（不误拒）", async () => {
    stubFetch(FILLS, SIGNALS);
    renderPanel(RECORD("a".repeat(64)));
    await screen.findByTestId("replay-position-flow");
  });
});

describe("明细源失败降级与分页全量（fix-replay-presentation-pipeline FE-1 修复面）", () => {
  beforeEach(() => {
    mockFills.mockReset();
    mockSignals.mockReset();
    mockChart.mock.calls.length = 0;
    mockOhlcv.mockReset();
    mockOhlcv.mockResolvedValue({ data: OHLCV } as never);
    mockEquity.mockReset();
    mockEquity.mockResolvedValue({ snapshots: [],
                                   has_more: false } as never);
    Element.prototype.scrollIntoView = vi.fn();
  });

  /** 失败形：服务层 ApiError 仿真（kind 按状态分层——传输细节归服务层） */
  function stubFail(status: number) {
    const err = status === 422
      ? new ApiError("validation", "请求参数有误", status)
      : new ApiError("server", "服务暂不可用，请稍后重试", status);
    mockFills.mockRejectedValue(err);
    mockSignals.mockRejectedValue(err);
  }

  /** 分页形成功 stub：按 params.offset 分派页（服务层单页函数契约形） */
  function stubPaged(
    pages: Record<number, { fills?: unknown[]; signals?: unknown[];
                            has_more?: boolean }>,
  ) {
    mockFills.mockImplementation(
      (_taskId: string, params?: { offset?: number }) => {
        const page = pages[params?.offset ?? 0] ?? {};
        return Promise.resolve({
          fills: page.fills ?? [],
          has_more: page.has_more ?? false } as never);
      });
    mockSignals.mockImplementation(
      (_taskId: string, params?: { offset?: number }) => {
        const page = pages[params?.offset ?? 0] ?? {};
        return Promise.resolve({
          signals: page.signals ?? [],
          has_more: page.has_more ?? false } as never);
      });
  }

  it.each([422, 503])(
    "失败形 HTTP %i：明细源不可达出现+「无成交」不出现+流水不部分呈现（3.2）",
    async (status) => {
      stubFail(status);
      renderPanel(RECORD("task-err"));
      expect(await screen.findByText(/明细源不可达/)).toBeInTheDocument();
      expect(screen.queryByText(/无成交/)).not.toBeInTheDocument();
      expect(screen.queryByTestId("replay-position-flow"))
        .not.toBeInTheDocument();
    });

  it("网络错形（fetch reject——非 HTTP 状态形）：明细源不可达+「无成交」"
      + "不出现（design §4 mock 形族补齐）", async () => {
    mockFills.mockRejectedValue(
      new ApiError("network", "网络连接失败，请检查网络后重试"));
    mockSignals.mockRejectedValue(
      new ApiError("network", "网络连接失败，请检查网络后重试"));
    renderPanel(RECORD("task-net"));
    expect(await screen.findByText(/明细源不可达/)).toBeInTheDocument();
    expect(screen.queryByText(/无成交/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("replay-position-flow"))
      .not.toBeInTheDocument();
  });

  it("单通道失败组合：signals 503 而 fills 200 → 整体降级不部分呈现"
      + "（Scenario WHEN「fills/signals 返回非 2xx」任一语义）", async () => {
    mockFills.mockResolvedValue(
      { fills: FILLS, has_more: false } as never);
    mockSignals.mockRejectedValue(
      new ApiError("server", "服务暂不可用，请稍后重试", 503));
    renderPanel(RECORD("task-half"));
    expect(await screen.findByText(/明细源不可达/)).toBeInTheDocument();
    // fills 数据已到达但不部分呈现（半量数据=病灶族根因教训）
    expect(screen.queryByTestId("replay-position-flow"))
      .not.toBeInTheDocument();
    expect(screen.queryByText(/无成交/)).not.toBeInTheDocument();
  });

  it("重试入口接通：失败→重试→成功后四件套呈现", async () => {
    stubFail(503);
    renderPanel(RECORD("task-retry"));
    expect(await screen.findByText(/明细源不可达/)).toBeInTheDocument();
    stubPaged({ 0: { fills: FILLS, signals: SIGNALS } });
    fireEvent.click(screen.getByRole("button", { name: /重试/ }));
    await screen.findByTestId("replay-position-flow");
    expect(screen.getByTestId("replay-flow-row-0")).toBeInTheDocument();
  });

  it("分页全量：500+2 两页→502 行无尾部截断（3.1 组件面——超上限"
      + " 不丢尾）", async () => {
    const big = Array.from({ length: 502 }, (_, i) => ({
      bar_time: new Date(Date.UTC(2026, 0, 1) + i * 3600_000).toISOString(),
      seq: i + 1, kind: "open", side: "long", price: 100,
      quantity: 1, fee: 0, realized_pnl: null, reason: null,
    }));
    stubPaged({ 0: { fills: big.slice(0, 500), signals: SIGNALS,
                      has_more: true },
                500: { fills: big.slice(500) } });
    renderPanel(RECORD("task-big"));
    await waitFor(() => {
      expect(document.querySelectorAll('[data-testid^="replay-flow-row-"]'))
        .toHaveLength(502);
    });
    // 六态标记面同全量（Scenario THEN「六态标记覆盖全部笔」——
    // 502 标记无尾部截断）
    expect((lastChartProps().signalMarkers ?? []).length).toBe(502);
    // 互达尾部面：第二页行（offset=500 的末笔）点击可达（Scenario
    // THEN「互达覆盖全部笔」——聚合尾部不丢锚）
    fireEvent.click(screen.getByTestId("replay-flow-row-501"));
    await waitFor(() => {
      expect(lastChartProps().selectedMarkerTime)
        .toBe(epochOf(big[501].bar_time));
    });
  });

  it("同 bar 多笔行为锚定（3.4 现状锁定）：两笔各成行成标+图→行定位"
      + " 该 bar 首笔", async () => {
    const sameBar = [
      { ...FILLS[0], seq: 1, kind: "open" },
      { ...FILLS[0], seq: 2, kind: "add", quantity: 1 },
    ];
    stubPaged({ 0: { fills: sameBar, signals: SIGNALS } });
    renderPanel(RECORD("task-same"));
    await waitFor(() => {
      expect((lastChartProps().signalMarkers ?? []).length).toBe(2);
    });
    expect(screen.getByTestId("replay-flow-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("replay-flow-row-1")).toBeInTheDocument();
    lastChartProps().onMarkerClick!(epochOf(sameBar[0].bar_time)!);
    await waitFor(() => {
      expect(screen.getByTestId("replay-flow-row-0").className)
        .toContain("bg-surface-active");
    });
  });

  it("exit_max_hold 平仓标记+流水动作中文；未知 kind 如实标注不滤除"
      + "（3.5）", async () => {
    const fills = [
      { ...FILLS[0], kind: "exit_max_hold", side: "long" },
      { ...FILLS[1], kind: "future_kind", side: "long", seq: 2 },
    ];
    stubPaged({ 0: { fills, signals: SIGNALS } });
    renderPanel(RECORD("task-vocab"));
    await waitFor(() => {
      const texts = (lastChartProps().signalMarkers ?? []).map((m) => m.text);
      expect(texts).toEqual(["平多", "未识别动作（future_kind）"]);
    });
    const row0 = screen.getByTestId("replay-flow-row-0");
    expect(row0.textContent).toContain("平多");
    expect(row0.textContent).not.toContain("exit_max_hold");
    expect(screen.getByTestId("replay-flow-row-1").textContent)
      .toContain("未识别动作（future_kind）");
  });

  it("signalToPanel 坏值形收窄（CARD-01 类型边界契约）：非字符串值"
      + "如实串化/回退不丢行", async () => {
    const badSignals = [{
      bar_time: "2025-12-31T20:00:00Z",
      exec_bar_time: "2026-01-01T00:00:00Z",
      kind: "open", outcome: "filled",
      fill_seq: 1, reject_reason: null,
      gene_hits: [
        // indicator 数值串化；condition 非 dict（bool 坏值）回退省略；
        // dict 形制真值（C4 D7——生产形制）
        { indicator: 123, field: 456, condition: true },
        { indicator: "EMA", condition: { type: "gt", threshold: 2 } },
      ],
    }];
    mockFills.mockResolvedValue(
      { fills: FILLS.slice(0, 1), has_more: false } as never);
    mockSignals.mockResolvedValue(
      { signals: badSignals, has_more: false } as never);
    renderPanel(RECORD("task-bad"));
    await screen.findByTestId("replay-flow-row-0");
    fireEvent.click(screen.getByTestId("replay-flow-row-0"));
    await waitFor(() => {
      // 「123 」尾空格=geneDescription 两段拼接形制（第一段尾空格在
      // trim 作用域外）——呈现级细节非缺陷，断言按实际形制锚定
      expect(screen.getByText(/基因：123 ；EMA > 2/)).toBeInTheDocument();
    });
  });

  it("signal 透传组件面：react-query AbortSignal 经 fetchPage 到服务层"
      + " 第三参（CARD-01 取消链——queryFn ({signal}) 注入路径）", async () => {
    stubPaged({ 0: { fills: FILLS, signals: SIGNALS } });
    renderPanel(RECORD("task-sig"));
    await screen.findByTestId("replay-flow-row-0");
    expect(mockFills.mock.calls[0][2]).toBeInstanceOf(AbortSignal);
    expect(mockSignals.mock.calls[0][2]).toBeInstanceOf(AbortSignal);
  });
});

describe("C4 详情完整面（参数回显/权益曲线/多笔高亮）", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    mockChart.mock.calls.length = 0;
    mockOhlcv.mockReset();
    mockOhlcv.mockResolvedValue({ data: OHLCV } as never);
    mockFills.mockReset();
    mockFills.mockResolvedValue(
      { fills: FILLS, has_more: false } as never);
    mockSignals.mockReset();
    mockSignals.mockResolvedValue(
      { signals: SIGNALS, has_more: false } as never);
    mockEquity.mockReset();
    mockEquity.mockResolvedValue(
      { snapshots: [], has_more: false } as never);
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("参数回显全字段（D6）：params 真值+statement 空态「—」", async () => {
    renderPanel(RECORD("task-p6"));
    const panel = await screen.findByTestId("replay-params-panel");
    const text = panel.textContent ?? "";
    expect(text).toContain("策略陈述：—");
    expect(text).toContain("区间：2026-01-01 ~ 2026-01-31");
    expect(text).toContain("BTCUSDT · 4h");
    expect(text).toContain("费率 0.001");
    expect(text).toContain("滑点 0.0005");
    expect(text).toContain("初始资金 100000");
    expect(text).toContain("杠杆 1x");
  });

  it("参数回显缺失键降级（D6）：缺费滑资金杠杆显「未知」", async () => {
    renderPanel(RECORD("task-p6b", {
      symbol: "BTCUSDT", timeframe: "4h",
      start: "2026-01-01", end: "2026-01-31",
    }));
    const text = (await screen.findByTestId("replay-params-panel"))
      .textContent ?? "";
    expect(text).toContain("费率 未知");
    expect(text).toContain("初始资金 未知");
    expect(text).toContain("杠杆 未知");
  });

  it("权益曲线分页装载+摘要对账（D5）：两页聚合+末值对账一致"
      + "+副图 equity 通道", async () => {
    const page1 = Array.from({ length: 500 }, (_, i) => ({
      bar_time: new Date(Date.UTC(2026, 0, 1)
        + i * 3600_000).toISOString(),
      equity: 100000 + i,
    }));
    const page2 = [
      { bar_time: "2026-01-22T00:00:00Z", equity: 99880 },
    ];
    mockEquity.mockImplementation((_tid: string, params: unknown) => {
      const offset = (params as { offset: number }).offset;
      return Promise.resolve(
        (offset === 0
          ? { snapshots: page1, has_more: true }
          : { snapshots: page2, has_more: false }) as never);
    });
    renderPanel(RECORD("task-eq"));
    const summary = await screen.findByTestId("replay-equity-summary");
    expect(mockEquity).toHaveBeenCalledTimes(2);  // 两页聚合
    const text = summary.textContent ?? "";
    expect(text).toContain("100,000");  // 首值
    expect(text).toContain("99,880");   // 末值（含对账锚同值）
    expect(text).toContain("-0.12%");   // delta
    expect(text).toContain("（一致）");  // 末值=final_equity 对账
    await waitFor(() => {
      const props = mockChart.mock.calls[
        mockChart.mock.calls.length - 1][0] as {
          subChartType?: string; equityData?: unknown[] };
      expect(props.subChartType).toBe("equity");
      expect(props.equityData).toHaveLength(501);
    });
  });

  it("对账不一致如实标注（D5）：末值≠final_equity", async () => {
    mockEquity.mockResolvedValue({
      snapshots: [{ bar_time: "2026-01-01T00:00:00Z", equity: 99000 }],
      has_more: false } as never);
    renderPanel(RECORD("task-eq2"));
    const text = (await screen.findByTestId("replay-equity-summary"))
      .textContent ?? "";
    expect(text).toContain("（不一致——如实标注）");
  });

  it("权益源失败独立降级（D5）：重试入口+四件套不受影响", async () => {
    mockEquity.mockRejectedValue(
      new Error("equity down") as never);
    renderPanel(RECORD("task-eq3"));
    expect(await screen.findByTestId("replay-equity-error"))
      .toBeInTheDocument();
    expect(screen.getByTestId("replay-flow-row-0")).toBeInTheDocument();
  });

  it("权益快照空行如实（D5 空态）：空任务不呈现副图摘要", async () => {
    renderPanel(RECORD("task-eq4"));
    await screen.findByTestId("replay-position-flow");
    expect(screen.getByText(/权益快照为空/)).toBeInTheDocument();
  });

  it("同 bar 多笔高亮（Q3）：图→行命中该 bar 全部行并列高亮",
     async () => {
    const sameBar = [
      { ...FILLS[0], seq: 1, kind: "open" },
      { ...FILLS[0], seq: 2, kind: "add", quantity: 1 },
    ];
    mockFills.mockResolvedValue(
      { fills: sameBar, has_more: false } as never);
    renderPanel(RECORD("task-q3"));
    await waitFor(() => {
      expect(mockChart.mock.calls.length).toBeGreaterThan(0);
    });
    lastChartProps().onMarkerClick!(epochOf(sameBar[0].bar_time)!);
    await waitFor(() => {
      expect(screen.getByTestId("replay-flow-row-0").className)
        .toContain("bg-surface-active");
      expect(screen.getByTestId("replay-flow-row-1").className)
        .toContain("bg-surface-active");
    });
  });

  it("权益快照形制异常全滤（D5 空态四格·形制异常格）：equity 全"
      + " null 行如实标注不静默", async () => {
    mockEquity.mockResolvedValue({
      snapshots: [
        { bar_time: "2026-01-01T00:00:00Z", equity: null },
        { bar_time: "2026-01-02T00:00:00Z", equity: null },
      ],
      has_more: false } as never);
    renderPanel(RECORD("task-eq7"));
    expect(await screen.findByText(
      /权益快照形制异常（2 行值缺失——如实标注）/))
      .toBeInTheDocument();
  });

  it("权益快照形制异常部分滤（D5）：正常行摘要+缺失行计数后缀",
     async () => {
    mockEquity.mockResolvedValue({
      snapshots: [
        { bar_time: "2026-01-01T00:00:00Z", equity: 100000 },
        { bar_time: "2026-01-02T00:00:00Z", equity: null },
        { bar_time: "2026-01-03T00:00:00Z", equity: 99880 },
      ],
      has_more: false } as never);
    renderPanel(RECORD("task-eq8"));
    const text = (await screen.findByTestId("replay-equity-summary"))
      .textContent ?? "";
    expect(text).toContain("（一致）");  // 末值 99880 对账锚
    expect(text).toContain("｜1 行值缺失（形制异常）");
  });

  it("零成交平线非空态（D5 空态四格·平线格）：全区间初始资金"
      + "水平线摘要+equity 副图如实呈现", async () => {
    mockEquity.mockResolvedValue({
      snapshots: [
        { bar_time: "2026-01-01T00:00:00Z", equity: 100000 },
        { bar_time: "2026-01-02T00:00:00Z", equity: 100000 },
        { bar_time: "2026-01-03T00:00:00Z", equity: 100000 },
      ],
      has_more: false } as never);
    renderPanel({ ...RECORD("task-eq6"),
      result: { replay: { task_id: "task-eq6", status: "completed",
        total_trades: 0, total_pnl: 0, final_equity: 100000 } } });
    const text = (await screen.findByTestId("replay-equity-summary"))
      .textContent ?? "";
    expect(text).toContain("+0.00%");
    expect(screen.queryByText(/权益快照为空/)).toBeNull();
    await waitFor(() => {
      const props = mockChart.mock.calls[
        mockChart.mock.calls.length - 1][0] as {
          subChartType?: string; equityData?: unknown[] };
      expect(props.subChartType).toBe("equity");
      expect(props.equityData).toHaveLength(3);
    });
  });

  it("对账无锚值降级（D5）：final_equity 缺失显「未知（无锚值）」",
     async () => {
    mockEquity.mockResolvedValue({
      snapshots: [
        { bar_time: "2026-01-01T00:00:00Z", equity: 100000 },
      ],
      has_more: false } as never);
    // 工厂推断 result 带 final_equity——无锚值场景本身就是缺该键的
    // 变体（联合 {} 分支），字面量直配触发缺属检查，断言收窄
    // （运行时形制即此变体）
    const rec = { ...RECORD("task-eq9"),
      result: { replay: { task_id: "task-eq9", status: "completed",
        total_trades: 1, total_pnl: 0 } } };
    renderPanel(rec as ReturnType<typeof RECORD>);
    const text = (await screen.findByTestId("replay-equity-summary"))
      .textContent ?? "";
    expect(text).toContain("未知");
    expect(text).toContain("（无锚值）");
  });

  it("参数回显 statement 真值（D6 正向）：非空陈述直读呈现",
     async () => {
    renderPanel({ ...RECORD("task-p7"),
      statement: "BTC 20 日布林突破回测假设" });
    const text = (await screen.findByTestId("replay-params-panel"))
      .textContent ?? "";
    expect(text).toContain("策略陈述：BTC 20 日布林突破回测假设");
  });

  it("condition 字符串旧形制回退（D7 防御面）：非 dict 塞回 type"
      + "经词表翻译+无 [object Object] 坏值", async () => {
    mockSignals.mockResolvedValue({
      signals: [{
        bar_time: "2025-12-31T20:00:00Z",
        exec_bar_time: "2026-01-01T00:00:00Z",
        kind: "open", outcome: "filled",
        fill_seq: 1, reject_reason: null,
        gene_hits: [{ indicator: "OLD", condition: "lt" }],
      }], has_more: false } as never);
    renderPanel(RECORD("task-d7s"));
    fireEvent.click(await screen.findByTestId("replay-flow-row-0"));
    await waitFor(() => {
      expect(screen.getByText(/基因：OLD </)).toBeInTheDocument();
    });
    // spec THEN 文字显式锚：无 "[object Object]" 坏值（D7 病灶）
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });

  it("行点击替换式单选（CARD-02 回归锚）：点 A 再点 B 仅 B 高亮"
      + "（积累式多选回归防御）", async () => {
    renderPanel(RECORD("task-c02"));
    fireEvent.click(await screen.findByTestId("replay-flow-row-0"));
    await waitFor(() => {
      expect(screen.getByTestId("replay-flow-row-0").className)
        .toContain("bg-surface-active");
    });
    fireEvent.click(screen.getByTestId("replay-flow-row-1"));
    await waitFor(() => {
      expect(screen.getByTestId("replay-flow-row-1").className)
        .toContain("bg-surface-active");
      // 替换语义：前一行不再高亮（积累式实现下此处红）
      expect(screen.getByTestId("replay-flow-row-0").className)
        .not.toContain("bg-surface-active");
    });
  });

  it("参数回显空 params 全降级+非数值门（D6 阶段四盲区消解）："
      + "区间/币种/周期「未知」，字符串费率直读、字符串杠杆「未知」",
     async () => {
    renderPanel(RECORD("task-p8", {
      fee: "0.001", leverage: "2",
    }));
    const text = (await screen.findByTestId("replay-params-panel"))
      .textContent ?? "";
    expect(text).toContain("区间：未知 ~ 未知");
    expect(text).toContain("未知 · 未知");
    expect(text).toContain("费率 0.001");  // pv 字符串直读
    expect(text).toContain("杠杆 未知");   // paramNum 非数值门
  });

  it("权益首值为 0 除零防护（D5 阶段四盲区消解）：delta 显「—」"
      + "不产 NaN", async () => {
    mockEquity.mockResolvedValue({
      snapshots: [
        { bar_time: "2026-01-01T00:00:00Z", equity: 0 },
        { bar_time: "2026-01-02T00:00:00Z", equity: 5 },
      ],
      has_more: false } as never);
    renderPanel(RECORD("task-eq10"));
    const text = (await screen.findByTestId("replay-equity-summary"))
      .textContent ?? "";
    expect(text).toContain("（—）");
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("Infinity");
  });
});
