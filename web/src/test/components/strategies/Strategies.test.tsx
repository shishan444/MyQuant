/**
 * Tests for Strategies page component.
 * Covers: loading state, empty state, strategy list rendering, filter/search.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { ReactNode } from "react";

// Mock API services
vi.mock("@/services/strategies", () => ({
  getStrategies: vi.fn(),
  getStrategy: vi.fn(),
  createStrategy: vi.fn(),
  deleteStrategy: vi.fn(),
  updateStrategy: vi.fn(),
  runBacktest: vi.fn(),
  compareStrategies: vi.fn(),
}));

// Mock hooks - keep useStrategies real (delegates to mocked API), mock mutations
vi.mock("@/hooks/useStrategies", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useStrategies")>();
  return {
    ...actual,
    useDeleteStrategy: vi.fn(),
    useUpdateStrategy: vi.fn(),
  };
});

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useNavigate: vi.fn(() => vi.fn()),
    // useSearchParams 走真实实现（由 MemoryRouter 提供）——URL 化筛选测试需要真实读写
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock framer-motion
vi.mock("framer-motion", () => ({
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

import { Strategies } from "@/pages/Strategies";
import { useDeleteStrategy } from "@/hooks/useStrategies";
import { getStrategies, deleteStrategy } from "@/services/strategies";
import { toast } from "sonner";
import type { Strategy } from "@/types/api";

const mockGetStrategies = vi.mocked(getStrategies);
const mockDeleteStrategy = vi.mocked(deleteStrategy);
const mockToast = vi.mocked(toast);
const mockUseDeleteStrategy = vi.mocked(useDeleteStrategy);

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

function makeStrategy(overrides: Partial<Strategy> = {}): Strategy {
  return {
    strategy_id: "s1",
    name: "Test Strategy",
    symbol: "BTCUSDT",
    timeframe: "4h",
    source: "lab",
    dna: { signal_genes: [], logic_genes: { entry_logic: "AND", exit_logic: "OR" }, execution_genes: { timeframe: "4h", symbol: "BTCUSDT" }, risk_genes: { stop_loss: 0.05, take_profit: 0.1, position_size: 0.3, leverage: 1, direction: "long" }, generation: 0, parent_ids: [], mutation_ops: [] },
    metrics: {
      annual_return: 0.25,
      sharpe_ratio: 1.5,
      max_drawdown: -0.08,
      win_rate: 0.6,
      total_trades: 50,
      profit_factor: 1.5,
      calmar_ratio: 1.2,
    },
    tags: "",
    notes: "",
    generation: 0,
    verify_count: 0,
    created_at: "2025-01-15T00:00:00Z",
    updated_at: "2025-01-15T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseDeleteStrategy.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as any);
});

describe("Strategies page", () => {
  // --- Loading state ---
  it("shows loading skeleton when loading", () => {
    // Don't resolve the API call -> stays in loading state
    mockGetStrategies.mockReturnValue(new Promise(() => {}));

    render(<Strategies />, { wrapper: createWrapper() });
    const pulses = document.querySelectorAll(".animate-pulse");
    expect(pulses.length).toBeGreaterThan(0);
  });

  // --- Error state ---
  it("shows error state with retry when loading fails", async () => {
    mockGetStrategies.mockRejectedValue(new Error("服务器错误"));

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("加载失败");
    expect(screen.getByText("服务器错误")).toBeInTheDocument();
    expect(screen.getByText(/重\s*试/)).toBeInTheDocument();
  });

  // --- Empty state ---
  it("shows empty state when no strategies", async () => {
    mockGetStrategies.mockResolvedValue({ items: [], total: 0 });

    render(<Strategies />, { wrapper: createWrapper() });
    // Wait for React Query to resolve
    await screen.findByText("策略库为空");
    expect(screen.getByText("前往实验室")).toBeInTheDocument();
  });

  // --- Strategy list ---
  it("renders strategy list with names", async () => {
    const strategies = [
      makeStrategy({ strategy_id: "s1", name: "Alpha Strategy" }),
      makeStrategy({ strategy_id: "s2", name: "Beta Strategy" }),
    ];
    mockGetStrategies.mockResolvedValue({ items: strategies, total: 2 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Alpha Strategy");
    expect(screen.getByText("Beta Strategy")).toBeInTheDocument();
  });

  // --- Strategy count ---
  it("shows strategy count", async () => {
    const strategies = [
      makeStrategy({ strategy_id: "s1", name: "First Strategy" }),
      makeStrategy({ strategy_id: "s2", name: "Second Strategy" }),
    ];
    mockGetStrategies.mockResolvedValue({ items: strategies, total: 2 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("已保存策略");
    // Count badge shows number of strategies
    expect(screen.getByText("已保存策略").nextElementSibling?.textContent).toBe("2");
  });

  // --- Filter: no match ---
  it("shows no match text when filter excludes all", async () => {
    mockGetStrategies.mockResolvedValue({ items: [makeStrategy({ strategy_id: "s1", name: "Alpha", source: "lab" })], total: 1 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Alpha");

    const searchInput = screen.getByPlaceholderText("搜索策略...");
    fireEvent.change(searchInput, { target: { value: "nonexistent" } });

    expect(screen.getByText("未找到匹配的策略")).toBeInTheDocument();
  });

  // --- Search by name ---
  it("filters strategies by search query", async () => {
    const strategies = [
      makeStrategy({ strategy_id: "s1", name: "Alpha Strategy" }),
      makeStrategy({ strategy_id: "s2", name: "Beta Strategy" }),
    ];
    mockGetStrategies.mockResolvedValue({ items: strategies, total: 2 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Alpha Strategy");

    const searchInput = screen.getByPlaceholderText("搜索策略...");
    fireEvent.change(searchInput, { target: { value: "alpha" } });

    expect(screen.getByText("Alpha Strategy")).toBeInTheDocument();
    expect(screen.queryByText("Beta Strategy")).not.toBeInTheDocument();
  });

  // --- Source badge ---
  it("renders source badge for strategies", async () => {
    mockGetStrategies.mockResolvedValue({ items: [makeStrategy({ strategy_id: "s1", source: "evolution" })], total: 1 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("进化");
  });

  // --- Symbol/timeframe ---
  it("renders symbol and timeframe", async () => {
    mockGetStrategies.mockResolvedValue({ items: [makeStrategy({ strategy_id: "s1", symbol: "ETHUSDT", timeframe: "1h" })], total: 1 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Test Strategy");
    const content = document.body.textContent ?? "";
    expect(content).toContain("ETHUSDT");
    expect(content).toContain("1h");
  });

  // --- Delete mutation hook called ---
  it("calls useDeleteStrategy", async () => {
    mockGetStrategies.mockResolvedValue({ items: [makeStrategy()], total: 1 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Test Strategy");
    expect(mockUseDeleteStrategy).toHaveBeenCalled();
  });

  // --- Qualified badge ---
  it("renders qualified badge for qualified strategy", async () => {
    mockGetStrategies.mockResolvedValue({
      items: [makeStrategy({ strategy_id: "s1", name: "Good Strat", qualified: true, best_fitness: 1.5 })],
      total: 1,
    });

    render(<Strategies />, { wrapper: createWrapper() });
    // 达标标记位于展开态详情面板：先点击策略名展开行
    fireEvent.click(await screen.findByText("Good Strat"));
    await screen.findByText("策略信息");
    const badges = screen.getAllByText("达标");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  // --- Checkbox rendering ---
  it("renders checkboxes in table rows", async () => {
    mockGetStrategies.mockResolvedValue({
      items: [
        makeStrategy({ strategy_id: "s1", name: "Alpha" }),
        makeStrategy({ strategy_id: "s2", name: "Beta" }),
      ],
      total: 2,
    });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Alpha");
    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    // 1 header + 2 rows = 3 checkboxes
    expect(checkboxes.length).toBe(3);
  });

  // --- Qualified filter ---
  it("shows qualified filter select", async () => {
    mockGetStrategies.mockResolvedValue({ items: [makeStrategy()], total: 1 });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Test Strategy");
    // 筛选下拉存在（断言语义保持：来源+验证两个筛选器）
    expect(screen.getByLabelText("来源筛选")).toBeInTheDocument();
    expect(screen.getByLabelText("验证状态筛选")).toBeInTheDocument();
  });

  // --- Expand panel on row click ---
  it("shows expand panel with strategy info when row name is clicked", async () => {
    mockGetStrategies.mockResolvedValue({
      items: [makeStrategy({ strategy_id: "s1", name: "Expandable" })],
      total: 1,
    });

    render(<Strategies />, { wrapper: createWrapper() });
    const nameEl = await screen.findByText("Expandable");
    fireEvent.click(nameEl);
    // Expand panel should show strategy info labels
    await screen.findByText("策略信息");
    expect(screen.getByText("策略结构")).toBeInTheDocument();
  });

  // --- Batch toolbar appears on selection ---
  it("shows batch toolbar when strategies are selected", async () => {
    mockGetStrategies.mockResolvedValue({
      items: [
        makeStrategy({ strategy_id: "s1", name: "Alpha" }),
        makeStrategy({ strategy_id: "s2", name: "Beta" }),
      ],
      total: 2,
    });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Alpha");

    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(checkboxes[1]); // Select first strategy row

    expect(screen.getByText(/已选 1 项/)).toBeInTheDocument();
    expect(screen.getByText("批量删除")).toBeInTheDocument();
  });

  // --- Batch delete feedback (three states: all success / partial / all failure) ---
  async function setupBatchDelete() {
    mockGetStrategies.mockResolvedValue({
      items: [
        makeStrategy({ strategy_id: "s1", name: "Alpha" }),
        makeStrategy({ strategy_id: "s2", name: "Beta" }),
      ],
      total: 2,
    });

    render(<Strategies />, { wrapper: createWrapper() });
    await screen.findByText("Alpha");

    // 选中全部两行（跳过 index 0 的表头全选框）；每次点击后重新查询——行样式随选中态重渲染会替换节点
    fireEvent.click(document.querySelectorAll('input[type="checkbox"]')[1]);
    fireEvent.click(document.querySelectorAll('input[type="checkbox"]')[2]);

    fireEvent.click(screen.getByText("批量删除"));
    fireEvent.click(await screen.findByText("删除全部"));
  }

  it("batch delete all success: shows success toast with count", async () => {
    mockDeleteStrategy.mockResolvedValue({} as never);
    await setupBatchDelete();

    await waitFor(() => {
      expect(mockToast.success).toHaveBeenCalledWith(expect.stringContaining("已删除 2"));
    });
    expect(mockToast.error).not.toHaveBeenCalled();
  });

  it("batch delete partial failure: shows failure counts, not silent", async () => {
    mockDeleteStrategy.mockImplementation((id: string) =>
      id === "s2" ? Promise.reject(new Error("delete failed")) : Promise.resolve({} as never),
    );
    await setupBatchDelete();

    await waitFor(() => {
      expect(mockToast.error).toHaveBeenCalledWith(expect.stringContaining("成功 1 个，失败 1 个"));
    });
  });

  it("batch delete all failure: shows zero-success counts", async () => {
    mockDeleteStrategy.mockRejectedValue(new Error("down"));
    await setupBatchDelete();

    await waitFor(() => {
      expect(mockToast.error).toHaveBeenCalledWith(expect.stringContaining("成功 0 个，失败 2 个"));
    });
  });
});
