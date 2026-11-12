/**
 * Tests for Trading page components: empty state, task list rendering, delete flow.
 * （新域形制：五态状态机/克隆语义/调度器徽标——/api/paper-trading）
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock 全部 hooks——importOriginal 取全量导出后仅覆写指定项
vi.mock("@/hooks/useTrading", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useTrading")>();
  return {
    ...actual,
    useTradingTasks: vi.fn(),
    useRunnerStatus: vi.fn(),
    useCreateTradingTask: vi.fn(),
    useDeleteTradingTask: vi.fn(),
    useCloneTradingTask: vi.fn(),
    useStopTradingTask: vi.fn(),
    useTradingFills: vi.fn(),
    useTradingSignals: vi.fn(),
    useTradingEquity: vi.fn(),
    useTradingFunding: vi.fn(),
    useTradingReport: vi.fn(),
    useTradingFinalReport: vi.fn(),
    useTradingWebSocket: vi.fn(() => false),
  };
});

vi.mock("react-router", () => ({
  useLocation: vi.fn(() => ({ state: null })),
  useNavigate: vi.fn(() => vi.fn()),
}));

// EquityLineChart 页面级 stub 有意保留（fe-chart-single-lib 审查 C-3① 裁决）：
// 本文件是 Trading 页级大测试，真组件渲染面过大；真组件的装配正确性（线色/
// setData 点数/降级形态）由 EquityLineChart.test 承载，此处仅验证页面组装锚
vi.mock("@/components/charts/EquityLineChart", () => ({
  EquityLineChart: () => <div data-testid="equity-curve-stub" />,
}));

// KlineChart（lightweight-charts）jsdom 无 canvas context——HUD 用例的 await
// 窗口内 rAF 异步绘制抛「Value is null」（run 级 unhandled）。非本文件被测面
// （页级组装锚在 TradingChart 层，ChartHud 在其内），stub 之（TradingChart.test 同形制）
vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: () => <div data-testid="kline-chart-stub" />,
}));

// useChartIndicators 真实调用会 fetch 后端（测试环境不可达）；TradingChart 对空 candleData
// 提前 return 装载骨架、SymbolInfoBar 不挂载——mock 两根蜡烛放行渲染（非本文件被测面）
vi.mock("@/hooks/useChartIndicators", () => ({
  useChartIndicators: vi.fn(() => ({
    candleData: [
      { timestamp: "2026-01-01 00:00:00", open: 100, high: 110, low: 95, close: 105, volume: 10 },
      { timestamp: "2026-01-01 04:00:00", open: 105, high: 115, low: 100, close: 112, volume: 20 },
    ],
    chartIndicators: [],
    chartBollData: undefined,
    volumeData: [],
    macdData: null,
    kdjData: null,
    isLoadingOhlcv: false,
    isLoadingIndicators: false,
  })),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { Trading } from "@/pages/Trading";
import {
  useTradingTasks,
  useDeleteTradingTask,
  useStopTradingTask,
  useCloneTradingTask,
  useRunnerStatus,
  useTradingFills,
  useTradingSignals,
  useTradingEquity,
  useTradingFunding,
  useTradingReport,
  useTradingFinalReport,
} from "@/hooks/useTrading";
import type { TradingTask } from "@/services/trading";
import type { PaperFill, PaperSignal } from "@/types/api";

const mockUseTradingTasks = vi.mocked(useTradingTasks);
const mockUseDeleteTradingTask = vi.mocked(useDeleteTradingTask);
const mockUseStopTradingTask = vi.mocked(useStopTradingTask);
const mockUseCloneTradingTask = vi.mocked(useCloneTradingTask);
const mockUseRunnerStatus = vi.mocked(useRunnerStatus);
const mockUseTradingFills = vi.mocked(useTradingFills);
const mockUseTradingSignals = vi.mocked(useTradingSignals);
const mockUseTradingEquity = vi.mocked(useTradingEquity);
const mockUseTradingFunding = vi.mocked(useTradingFunding);
const mockUseTradingReport = vi.mocked(useTradingReport);
const mockUseTradingFinalReport = vi.mocked(useTradingFinalReport);

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        {children}
      </QueryClientProvider>
    );
  };
}

const mockDeleteMutate = vi.fn();

/** mutation hook 缺省形态（isPending 等字段——TaskDetailPanel 消费） */
function mockMutation(mutate: ReturnType<typeof vi.fn>) {
  return {
    mutate,
    mutateAsync: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
    reset: vi.fn(),
    status: "idle",
    variables: undefined,
    data: undefined,
    error: null,
    context: undefined,
    failureCount: 0,
    failureReason: null,
    submittedAt: 0,
  };
}

/** 详情面板明细/报告 hooks 缺省返回（未加载态） */
const emptyQuery = (data: unknown) => ({
  data,
  isLoading: false,
  isError: false,
  isSuccess: true,
  error: null,
});

beforeEach(() => {
  vi.clearAllMocks();
  mockUseDeleteTradingTask.mockReturnValue(mockMutation(mockDeleteMutate) as any);
  mockUseStopTradingTask.mockReturnValue(mockMutation(vi.fn()) as any);
  mockUseCloneTradingTask.mockReturnValue(mockMutation(vi.fn()) as any);

  mockUseRunnerStatus.mockReturnValue({
    data: { max_running_tasks: 2, running_count: 0, active_task_ids: [], pending_depth: 0 },
    isLoading: false,
    isError: false,
    isSuccess: true,
  } as any);

  mockUseTradingFills.mockReturnValue(emptyQuery({ fills: [], limit: 500, offset: 0, has_more: false }) as any);
  mockUseTradingSignals.mockReturnValue(emptyQuery({ signals: [] }) as any);
  mockUseTradingEquity.mockReturnValue(emptyQuery({ snapshots: [], limit: 500, offset: 0, has_more: false }) as any);
  mockUseTradingFunding.mockReturnValue(emptyQuery({ records: [], limit: 500, offset: 0, has_more: false }) as any);
  mockUseTradingReport.mockReturnValue(emptyQuery(undefined) as any);
  mockUseTradingFinalReport.mockReturnValue(emptyQuery(undefined) as any);
});

function makeTask(overrides: Partial<TradingTask> = {}): TradingTask {
  return {
    task_id: "t1",
    status: "stopped",
    strategy_id: "11111111-2222-3333-4444-555555555555",
    symbol: "BTCUSDT",
    timeframe: "4h",
    initial_cash: 100000,
    fee: 0.001,
    slippage: 0.0005,
    leverage: 1,
    window_bars: 180,
    stop_loss_pct: null,
    bars_processed: 100,
    last_bar_time: null,
    balance: 102000,
    position_side: null,
    position_entry: null,
    position_quantity: null,
    position_margin: null,
    position_open_cost: null,
    position_sl_price: null,
    position_tp_price: null,
    position_trail_peak: null,
    position_entry_bar_seq: null,
    unrealized_pnl: 0,
    total_trades: 5,
    win_count: 3,
    loss_count: 2,
    total_pnl: 2000,
    research_status: "unverified",
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    started_at: null,
    stopped_at: "2025-01-02T00:00:00Z",
    stop_reason: "user_stop",
    heartbeat_at: null,
    error_detail: null,
    ...overrides,
  };
}

describe("Trading page", () => {
  // --- Loading state ---
  it("shows loading state", () => {
    mockUseTradingTasks.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      isSuccess: false,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  // --- Error state ---
  it("shows error state with retry when task loading fails", () => {
    const refetch = vi.fn();
    mockUseTradingTasks.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("网络错误"),
      refetch,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    expect(screen.getByText("加载失败")).toBeInTheDocument();
    expect(screen.getByText("网络错误")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/重\s*试/));
    expect(refetch).toHaveBeenCalled();
  });

  // --- Empty state ---
  it("shows empty state when no tasks", () => {
    mockUseTradingTasks.mockReturnValue({
      data: { tasks: [], total: 0 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    expect(screen.getByText("暂无模拟交易任务")).toBeInTheDocument();
    expect(screen.getByText("前往策略库")).toBeInTheDocument();
  });

  // --- Task list renders（新形制：symbol 主显+strategy_id 短显） ---
  it("renders task list with tasks", () => {
    const tasks = [
      makeTask({ task_id: "t1", status: "running", symbol: "BTCUSDT" }),
      makeTask({ task_id: "t2", status: "stopped", symbol: "ETHUSDT" }),
    ];
    mockUseTradingTasks.mockReturnValue({
      data: { tasks, total: 2 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    expect(screen.getAllByText("BTCUSDT").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("ETHUSDT").length).toBeGreaterThanOrEqual(1);
  });

  // --- 五态徽标 ---
  it("shows correct status badges for all five states", () => {
    const tasks = [
      makeTask({ task_id: "t1", status: "pending", symbol: "BTCUSDT" }),
      makeTask({ task_id: "t2", status: "running", symbol: "ETHUSDT" }),
      makeTask({ task_id: "t3", status: "completed", symbol: "SOLUSDT" }),
      makeTask({ task_id: "t4", status: "stopped", symbol: "XRPUSDT" }),
      makeTask({ task_id: "t5", status: "error", symbol: "DOGEUSDT" }),
    ];
    mockUseTradingTasks.mockReturnValue({
      data: { tasks, total: 5 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    expect(screen.getAllByText("排队中").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("运行中").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("已完结").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("已停止").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("异常").length).toBeGreaterThanOrEqual(1);
  });

  // --- error 态披露 error_detail ---
  it("discloses error_detail for error tasks", () => {
    const tasks = [
      makeTask({ task_id: "t1", status: "error", symbol: "BTCUSDT", error_detail: "ValueError: bad dna" }),
    ];
    mockUseTradingTasks.mockReturnValue({
      data: { tasks, total: 1 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    // error 任务非 running 不会被自动选中——点击侧栏任务进详情面板
    fireEvent.click(screen.getByText("BTCUSDT"));
    expect(screen.getByText(/ValueError: bad dna/)).toBeInTheDocument();
  });

  // --- Task selection ---
  it("auto-selects running task", () => {
    const tasks = [
      makeTask({ task_id: "t1", status: "running", symbol: "BTCUSDT" }),
    ];
    mockUseTradingTasks.mockReturnValue({
      data: { tasks, total: 1 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });
    // Master-Detail：币种同时出现在侧栏（span）与详情面板（h2）
    expect(screen.getAllByText("BTCUSDT").length).toBeGreaterThanOrEqual(2);
  });

  // --- 累计资金费接线（CARD-03：Trading.tsx → TradingChart → SymbolInfoBar） ---
  it("renders latest snapshot cumulative funding in symbol info bar", () => {
    // 锚「取尾条 .at(-1)」语义：首条 0.5/尾条 1.25——若取 .at(0) 则呈现 +0.5000（错值静默）
    const tasks = [
      makeTask({ task_id: "t1", status: "running", symbol: "BTCUSDT" }),
    ];
    mockUseTradingTasks.mockReturnValue({
      data: { tasks, total: 1 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);
    mockUseTradingEquity.mockReturnValue(
      emptyQuery({
        snapshots: [
          { task_id: "t1", bar_time: "2026-01-01 00:00:00", equity: 100000, balance: 100000, cumulative_funding: 0.5 },
          { task_id: "t1", bar_time: "2026-01-01 04:00:00", equity: 100010, balance: 100010, cumulative_funding: 1.25 },
        ],
        limit: 500,
        offset: 0,
        has_more: false,
      }) as any,
    );

    render(<Trading />, { wrapper: createWrapper() });
    // SymbolInfoBar 呈现尾条累计值（fmt 四位小数+非负带显式 +）
    expect(screen.getByText("+1.2500 USDT")).toBeInTheDocument();
  });

  // --- HUD 页级组装（fe-asset-data-domain 2.1——t3r5 G3：position 透传+hudSignals/fills 倒序） ---
  it("wires HUD with position passthrough and newest-first signals/fills", async () => {
    // 信号 fixture（API 正序=旧→新；seq 判别）
    const makeSignal = (seq: number, kind: string): PaperSignal =>
      ({
        id: seq, task_id: "t1", strategy_id: "s1",
        bar_time: `2026-11-01 0${seq}:00:00+00:00`, seq, signal_kind: kind,
        direction: "long", size_fraction: null, gene_hits: [], indicator_snapshot: {},
        outcome: "filled", reject_reason: null, fill_seq: seq, capped: null,
        equity: 100000, balance: 100000, unrealized_pnl: -50,
        position_side: "long", position_quantity: 0.01, position_margin: 50,
        created_at: "2026-11-01T00:00:00Z",
      }) as PaperSignal;
    // 成交 fixture（API 正序=旧→新；price 判别）
    const makeHudFill = (id: number, price: number): PaperFill =>
      ({
        id, task_id: "t1", bar_time: `2026-11-02 0${id}:00:00+00:00`, seq: id,
        kind: "open", side: "buy", price, quantity: 0.01, fee: 1,
        realized_pnl: null, reason: "gene_hit",
      }) as PaperFill;

    const task = makeTask({
      task_id: "t1", status: "running", symbol: "BTCUSDT", timeframe: "4h",
      position_side: "long", position_entry: 94000, position_quantity: 0.01,
      unrealized_pnl: -50,
    });
    mockUseTradingTasks.mockReturnValue({
      data: { tasks: [task], total: 1 },
      isLoading: false, isError: false, isSuccess: true,
    } as any);
    // 信号 API 正序两条（seq 1 open / seq 2 close）——Trading.tsx reverse 后 HUD 最新在前
    mockUseTradingSignals.mockReturnValue(
      emptyQuery({ signals: [makeSignal(1, "open"), makeSignal(2, "close")] }) as any,
    );
    // 成交 API 正序三笔——TradingChart slice(-2).reverse() 后 HUD 最近两笔倒序
    mockUseTradingFills.mockReturnValue(
      emptyQuery({
        fills: [makeHudFill(1, 91000), makeHudFill(2, 92000), makeHudFill(3, 93000)],
        limit: 500, offset: 0, has_more: false,
      }) as any,
    );

    render(<Trading />, { wrapper: createWrapper() });
    const hud = await screen.findByTestId("chart-hud");
    // position 透传（task 字段→HUD 仓位浮层真值——:595-600 映射）
    expect(hud.textContent).toContain("做多");
    expect(hud.textContent).toContain("开仓 94000.00");
    expect(hud.textContent).toContain("浮盈亏 -50.00");
    // hudSignals reverse（Trading.tsx:601）：最新 seq 2（平仓）呈现在 seq 1（开仓·filled）之前
    const text = hud.textContent ?? "";
    expect(text.indexOf("平仓 · filled")).toBeLessThan(text.indexOf("开仓 · filled"));
    // fills slice(-2).reverse()（TradingChart）：最近两笔在场且最新在前，最旧 91000 被截
    expect(text.indexOf("93000.00")).toBeLessThan(text.indexOf("92000.00"));
    expect(text).not.toContain("91000.00");
  });

  // --- Delete confirmation flow ---
  it("calls delete mutation on confirm", async () => {
    const task = makeTask({ task_id: "t1", status: "stopped" });
    mockUseTradingTasks.mockReturnValue({
      data: { tasks: [task], total: 1 },
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as any);

    render(<Trading />, { wrapper: createWrapper() });

    expect(mockUseDeleteTradingTask).toHaveBeenCalled();
  });
});
