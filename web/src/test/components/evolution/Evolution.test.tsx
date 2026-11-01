/**
 * Tests for Evolution page component.
 * Covers: loading state, empty state, readonly params panel (alignment-batch D3
 * ——发起归 MCP trigger_evolution，配置表单已移除), historical tasks.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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

// Mock motion/react
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

// Mock evolution sub-components
vi.mock("@/components/evolution/SegmentedControl", () => ({
  SegmentedControl: ({ value, onChange }: any) => (
    <div data-testid="segmented-control" data-value={value}>
      <button onClick={() => onChange("auto")}>Auto</button>
      <button onClick={() => onChange("seed")}>Seed</button>
    </div>
  ),
}));

vi.mock("@/components/evolution/AutoConfigForm", () => ({
  AutoConfigForm: () => <div data-testid="auto-config-form">Auto Config</div>,
}));

vi.mock("@/components/evolution/SeedConfigForm", () => ({
  SeedConfigForm: () => <div data-testid="seed-config-form">Seed Config</div>,
}));

vi.mock("@/components/evolution/ProgressPanel", () => ({
  ProgressPanel: () => <div data-testid="progress-panel">Progress</div>,
}));

vi.mock("@/components/evolution/ScoreTrendChart", () => ({
  ScoreTrendChart: () => <div data-testid="score-trend-chart">Chart</div>,
}));

vi.mock("@/components/evolution/StrategyList", () => ({
  StrategyList: () => <div data-testid="strategy-list">Strategies</div>,
}));

vi.mock("@/components/evolution/HistoryTable", () => ({
  HistoryTable: () => <div data-testid="history-table">History</div>,
}));

vi.mock("@/components/evolution/QuickPresets", () => ({
  QuickPresets: () => <div data-testid="quick-presets">Presets</div>,
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

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: ({ className }: any) => <div className={className} data-testid="skeleton" />,
}));

import { Evolution } from "@/pages/Evolution";
import { getEvolutionTasks } from "@/services/evolution";
import type { EvolutionTask } from "@/types/api";

const mockGetEvolutionTasks = vi.mocked(getEvolutionTasks);

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
  // --- Loading state ---
  it("shows loading skeleton when loading", () => {
    mockGetEvolutionTasks.mockReturnValue(new Promise(() => {}));

    render(<Evolution />, { wrapper: createWrapper() });
    const skeletons = document.querySelectorAll(".ant-skeleton"); // AntD Skeleton（断言语义保持：加载占位存在）
    expect(skeletons.length).toBeGreaterThanOrEqual(3);
  });

  // --- Empty state (no active task): readonly params area + MCP channel notice ---
  it("renders readonly params area with MCP notice when no active task", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("当前没有进化任务");
    // 渠道披露：头注与空态段同屏，至少一处（getAllBy 容纳多处）
    expect(screen.getAllByText(/MCP trigger_evolution/).length).toBeGreaterThan(0);
    // D3：发起控件移除——配置表单不在
    expect(screen.queryByTestId("segmented-control")).not.toBeInTheDocument();
    expect(screen.queryByTestId("auto-config-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("seed-config-form")).not.toBeInTheDocument();
  });

  // --- Readonly params (no launch form by default) ---
  it("does not render launch form (readonly-only page)", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("任务参数");
    expect(screen.queryByTestId("segmented-control")).not.toBeInTheDocument();
  });

  // --- Progress section renders ---
  it("renders progress section", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("探索进度");
  });

  // --- Empty state message ---
  it("shows empty state when no tasks at all", async () => {
    mockGetEvolutionTasks.mockResolvedValue({ items: [], total: 0 });

    render(<Evolution />, { wrapper: createWrapper() });
    await screen.findByText("还没有进化任务");
  });

  // --- With completed tasks ---
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
    await screen.findByText("历史探索");
    expect(screen.getByTestId("history-table")).toBeInTheDocument();
  });
});
