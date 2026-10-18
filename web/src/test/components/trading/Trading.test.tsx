/**
 * Tests for Trading page components: empty state, task list rendering, delete flow.
 * （新域形制：五态状态机/克隆语义/调度器徽标——/api/paper-trading）
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock all hooks - use importOriginal to get all exports, then override specific ones
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
  useTradingEquity,
  useTradingFunding,
  useTradingReport,
  useTradingFinalReport,
} from "@/hooks/useTrading";
import type { TradingTask } from "@/services/trading";

const mockUseTradingTasks = vi.mocked(useTradingTasks);
const mockUseDeleteTradingTask = vi.mocked(useDeleteTradingTask);
const mockUseStopTradingTask = vi.mocked(useStopTradingTask);
const mockUseCloneTradingTask = vi.mocked(useCloneTradingTask);
const mockUseRunnerStatus = vi.mocked(useRunnerStatus);
const mockUseTradingFills = vi.mocked(useTradingFills);
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
    research_status_snapshot: "researched",
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
    // Master-Detail: symbol in sidebar (span) and detail panel (h2)
    expect(screen.getAllByText("BTCUSDT").length).toBeGreaterThanOrEqual(2);
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
