/**
 * Evolution 页组件测试。覆盖：加载态、空态、只读参数面板
 * （alignment-batch D3——发起归 MCP trigger_evolution，配置表单
 * 已移除）、历史任务。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock API services
vi.mock("@/services/evolution", () => ({
  getEvolutionTasks: vi.fn(),
  getEvolutionTask: vi.fn(),
  getEvolutionHistory: vi.fn(),
  getDiscoveredStrategies: vi.fn(),
  getAllDiscoveredStrategies: vi.fn(),
  getTaskStrategies: vi.fn(),
  createEvolutionTask: vi.fn(),
  stopEvolutionTask: vi.fn(),
  pauseEvolutionTask: vi.fn(),
  resumeEvolutionTask: vi.fn(),
}));

vi.mock("@/services/strategies", () => ({
  getStrategies: vi.fn(),
  getStrategy: vi.fn(),
  createStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  updateStrategy: vi.fn(),
  runBacktest: vi.fn(),
}));

vi.mock("@/services/datasets", () => ({
  getAvailableSources: vi.fn(),
}));

vi.mock("react-router", () => ({
  useNavigate: vi.fn(() => vi.fn()),
  useLocation: vi.fn(() => ({ state: null })),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// mock motion/react
vi.mock("motion/react", () => ({
  useReducedMotion: () => false,
  motion: new Proxy({}, {
    get: (_, tag) => {
      return ({ children, ...props }: any) => {
        const Tag = typeof tag === "string" ? tag : "div";
        const htmlProps: any = {};
        for (const [k, v] of Object.entries(props)) {
          if (typeof v !== "object" || k === "className" || k === "style" || k === "onClick" || k === "aria-label") {
            htmlProps[k] = v;
          }
        }
        return <Tag {...htmlProps}>{children}</Tag>;
      };
    },
  }),
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

// mock 进化子组件（五死件 AutoConfigForm/SeedConfigForm/QuickPresets/
// ProgressPanel/SegmentedControl 已随 fe-research-execution-domain 3.3 移除）
vi.mock("@/components/evolution/ScoreTrendChart", () => ({
  ScoreTrendChart: () => <div data-testid="score-trend-chart">Chart</div>,
}));

vi.mock("@/components/evolution/StrategyList", () => ({
  StrategyList: () => <div data-testid="strategy-list">Strategies</div>,
}));

vi.mock("@/components/evolution/HistoryTable", () => ({
  HistoryTable: () => <div data-testid="history-table">History</div>,
}));

vi.mock("@/components/evolution/TaskDetailDrawer", () => ({
  TaskDetailDrawer: () => null,
}));

vi.mock("@/components/PageTransition", () => ({
  PageTransition: ({ children }: any) => <>{children}</>,
}));

vi.mock("@/components/GlassCard", () => ({
  GlassCard: ({ children, ...props }: any) => <div {...props}>{children}</div>,
}));

vi.mock("@/components/EmptyState", () => ({
  EmptyState: ({ title }: any) => <div data-testid="empty-state">{title}</div>,
}));

vi.mock("@/components/ConfirmDialog", () => ({
  ConfirmDialog: () => null,
}));
// StopConfirmDialog 真渲染（t3r4 G1b——停止流链断言需要真对话框 DOM；
// 组件级三要件锚在 StopConfirmDialog.test）

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: ({ className }: any) => <div className={className} data-testid="skeleton" />,
}));

import { Evolution } from "@/pages/Evolution";
import { getEvolutionTasks, stopEvolutionTask, pauseEvolutionTask } from "@/services/evolution";
import type { EvolutionTask } from "@/types/api";
import { toast } from "sonner";

const mockGetEvolutionTasks = vi.mocked(getEvolutionTasks);
const mockStop = vi.mocked(stopEvolutionTask);
const mockPause = vi.mocked(pauseEvolutionTask);

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Evolution page", () => {
  // --- 加载态 ---
  it("shows loading skeleton when loading", () => {
    mockGetEvolutionTasks.mockReturnValue(new Promise(() => {}));

    render(<Evolution />, { wrapper: createWrapper() });
    const skeletons = document.querySelectorAll(".ant-skeleton"); // AntD Skeleton（断言语义保持：加载占位存在）
    expect(skeletons.length).toBeGreaterThanOrEqual(3);
  });

  // --- 空态（无活动任务）：只读参数区+MCP 渠道提示 ---
  it("renders readonly params area with MCP notice when no active task", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("当前没有进化任务");
    // 渠道披露：头注与空态段同屏，至少一处（getAllBy 容纳多处）
    expect(screen.getAllByText(/MCP trigger_evolution/).length).toBeGreaterThan(0);
    // D3：发起控件移除（fe-research-execution-domain 3.3 死件已物理删除）
    expect(screen.queryByTestId("segmented-control")).not.toBeInTheDocument();
    expect(screen.queryByTestId("auto-config-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("seed-config-form")).not.toBeInTheDocument();
  });

  // --- 只读参数（缺省无发起表单） ---
  it("does not render launch form (readonly-only page)", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("任务参数");
    expect(screen.queryByTestId("segmented-control")).not.toBeInTheDocument();
  });

  // --- 三列布局（fe-research-execution-domain 3.1：参数/趋势/发现策略常驻） ---
  it("renders three-column sections (params / trend / discovered)", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("任务参数");
    expect(screen.getByText("评分趋势 · 历代最优")).toBeInTheDocument();
    expect(screen.getByText(/发现策略 · 前沿个体/)).toBeInTheDocument();
  });

  // --- 空态消息（顶部条空态；EmptyState 卡已随布局重排退役） ---
  it("shows empty state when no tasks at all", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("当前没有进化任务");
  });

  // --- 有完成任务时 ---
  it("renders history section with completed tasks", async () => {
    const tasks: EvolutionTask[] = [
      {
        task_id: "e1",
        status: "completed",
        symbol: "BTCUSDT",
        timeframe: "4h",
        mode: "auto",
        score_template: "explorer",
        target_score: 80,
        population_size: 30,
        leverage: 1,
        direction: "long",
        current_generation: 10,
        max_generations: 10,
        elite_ratio: 0.5,
        continuous: true,
        strategy_threshold: 80,
        strategy_count: 0,
        exploration_efficiency: 0,
        min_annual_return: 0.1,
        data_row_count: 0,
        best_score: 85,
        best_fitness: 0.82,
        qualified_count: 3,
        created_at: "2025-01-01T00:00:00Z",
        updated_at: "2025-01-01T00:00:00Z",
      },
    ];
    mockGetEvolutionTasks.mockResolvedValue({ items: tasks, total: 1 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("历史任务");
    expect(screen.getByTestId("history-table")).toBeInTheDocument();
  });
});


describe("Evolution 停止流链（t3r4 G1b/G6——spec R3 停止确认流）", () => {
  /** running 任务蓝本（控制钮渲染条件 isActive） */
  function runningTask(): EvolutionTask {
    return {
      task_id: "task-run-stop-1",
      status: "running",
      symbol: "BTCUSDT",
      timeframe: "4h",
      mode: "auto",
      score_template: "balanced",
      target_score: 80,
      population_size: 15,
      max_generations: 50,
      elite_ratio: 0.5,
      current_generation: 7,
      created_at: new Date(Date.now() - 5000).toISOString(),
      updated_at: new Date().toISOString(),
      best_score: 66.6,
      best_fitness: 2.5,
      qualified_count: 0,
      leverage: 2,
      direction: "long",
      data_start: "2026-01-01",
      data_end: "2026-06-30",
      data_row_count: 100,
      indicator_pool: ["rsi"],
      continuous: false,
      strategy_threshold: 80,
      strategy_count: 0,
      exploration_efficiency: 0,
      min_annual_return: 0.1,
    } as EvolutionTask;
  }

  it("running 态点停止→对话框三要件→确认停止→stopEvolutionTask(task_id)", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [runningTask()], total: 1 } as never);
    mockStop.mockResolvedValue(undefined as never);

    render(<Evolution />, { wrapper: createWrapper() });
    // 顶部条控制钮（运行控制语义标注文案）
    const stopBtn = await screen.findByRole("button", { name: /停止（当前代落库后退出）/ });
    fireEvent.click(stopBtn);
    // 对话框三要件真渲染（07-M1：标题问句+事实三句+双钮）
    expect(await screen.findByText("停止该任务？")).toBeInTheDocument();
    expect(screen.getByText(/停止即当前代完整落库后任务退出/)).toBeInTheDocument();
    expect(screen.getByText(/任务数据保留，历史记录与产出策略仍可查看/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("stop-confirm-button"));
    await waitFor(() => {
      expect(mockStop).toHaveBeenCalledWith(
        "task-run-stop-1", expect.anything(),
      );
    });
  });

  it("暂停接线：running 态点暂停→pauseEvolutionTask(task_id)", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [runningTask()], total: 1 } as never);
    mockPause.mockResolvedValue(undefined as never);

    render(<Evolution />, { wrapper: createWrapper() });
    const pauseBtn = await screen.findByRole("button", { name: /^暂停$/ });
    fireEvent.click(pauseBtn);
    await waitFor(() => {
      expect(mockPause).toHaveBeenCalledWith(
        "task-run-stop-1", expect.anything(),
      );
    });
  });

  it("409 如实呈现：停止失败→toast.error 携后端 detail（构造 IllegalTransition）", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [runningTask()], total: 1 } as never);
    mockStop.mockRejectedValue(new Error("操作失败: 状态不允许——任务已处于终态") as never);

    render(<Evolution />, { wrapper: createWrapper() });
    const stopBtn = await screen.findByRole("button", { name: /停止（当前代落库后退出）/ });
    fireEvent.click(stopBtn);
    fireEvent.click(await screen.findByTestId("stop-confirm-button"));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("状态不允许"),
      );
    });
  });
});
