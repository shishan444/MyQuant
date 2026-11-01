/**
 * 05 进化中心对齐批单测（alignment-batch D3）。
 * 任务参数只读回显（无发起表单——发起归 MCP trigger_evolution）+
 * 进度=已耗时+状态灯（无进度条）+champion/snapshot 两源标注。
 * 服务层全 mock（零外网红线）；WS 连接层 mock。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

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
  createStrategy: vi.fn(),
}));

vi.mock("@/realtime/connection", () => ({
  createRealtimeConnection: () => ({
    onMessage: vi.fn(),
    close: vi.fn(),
  }),
}));

import { Evolution } from "@/pages/Evolution";
import * as evo from "@/services/evolution";
import { formatElapsed } from "@/components/evolution/taskRuntimeUtils";

const DNA_MIN = {
  signal_genes: [],
  logic_genes: { entry_logic: "AND", exit_logic: "OR" },
  risk_genes: { direction: "long", leverage: 1, sl_mode: "pct", stop_loss: 0.05, take_profit: 0.1, position_size: 0.3 },
  execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
  layers: null,
};

/** 运行中任务：created_at=5 秒前（elapsed 断言稳定落在「N秒」档） */
function mkRunningTask() {
  return {
    task_id: "task-run-1",
    status: "running",
    target_score: 80,
    score_template: "balanced",
    symbol: "BTCUSDT",
    timeframe: "4h",
    initial_dna: null,
    champion_dna: null,
    population_size: 15,
    max_generations: 50,
    elite_ratio: 0.5,
    current_generation: 7,
    created_at: new Date(Date.now() - 5000).toISOString(),
    updated_at: new Date(Date.now() - 1000).toISOString(),
    stop_reason: null,
    best_score: 66.6,
    best_fitness: 2.5,
    requirements: { objective: "sharpe" },
    qualified_count: 0,
    leverage: 2,
    direction: "long",
    data_start: "2026-01-01",
    data_end: "2026-06-30",
    data_time_start: "2026-01-01T00:00:00",
    data_time_end: "2026-06-29T20:00:00",
    data_row_count: 1200,
    indicator_pool: ["ema", "rsi"],
    timeframe_pool: ["4h", "1h"],
    mode: "auto",
    continuous: false,
    strategy_threshold: 80,
    min_annual_return: 0.1,
    max_drawdown_limit: null,
    strategy_count: 0,
    exploration_efficiency: 0,
    current_phase: "evolution_running",
    progress_json: null,
  };
}

const COMPLETED_TASK = {
  ...mkRunningTask(),
  task_id: "task-done-1",
  status: "completed",
};

const TASK_STRATEGIES = {
  task_id: "task-run-1",
  strategies: [
    { strategy_id: "champ-1", dna: DNA_MIN, source: "champion", score: 88.8 },
    { strategy_id: "snap-1", dna: DNA_MIN, source: "snapshot", generation: 5, score: 80.2 },
  ],
};

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Evolution />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(evo.getEvolutionTasks).mockResolvedValue({ items: [mkRunningTask(), COMPLETED_TASK] as never, total: 2 } as never);
  vi.mocked(evo.getEvolutionTask).mockResolvedValue(mkRunningTask() as never);
  vi.mocked(evo.getEvolutionHistory).mockResolvedValue({ records: [], total: 0 } as never);
  vi.mocked(evo.getDiscoveredStrategies).mockResolvedValue([] as never);
  vi.mocked(evo.getAllDiscoveredStrategies).mockResolvedValue([] as never);
  vi.mocked(evo.getTaskStrategies).mockResolvedValue(TASK_STRATEGIES as never);
});

describe("Evolution D3 任务参数只读回显", () => {
  it("呈现任务参数（品种/种群/代数/指标池/达标目标），无发起表单与开始按钮", async () => {
    setup();
    const params = await screen.findByTestId("task-params");
    expect(params).toHaveTextContent("BTCUSDT");
    expect(params).toHaveTextContent("15");
    expect(params).toHaveTextContent("50");
    expect(params).toHaveTextContent("2 项");
    expect(params).toHaveTextContent("sharpe");
    // 发起控件移除：无「开始探索」类按钮，无配置表单标题
    expect(screen.queryByText("探索配置")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /开始探索|开始/ })).not.toBeInTheDocument();
    // 只读渠道说明披露
    expect(screen.getByText(/MCP trigger_evolution/)).toBeInTheDocument();
  });

  it("无任务时：空态说明发起渠道（不出现参数表）", async () => {
    vi.mocked(evo.getEvolutionTasks).mockResolvedValue({ items: [], total: 0 } as never);
    setup();
    expect(await screen.findByText("当前没有进化任务")).toBeInTheDocument();
    expect(screen.queryByTestId("task-params")).not.toBeInTheDocument();
  });
});

describe("Evolution D3 进度=已耗时+状态灯（无进度条）", () => {
  it("状态灯按后端真实 status 值域映射（running→蓝）", async () => {
    setup();
    const light = await screen.findByTestId("task-status-light");
    expect(light).toHaveAttribute("data-status", "running");
    expect(light.className).toContain("bg-blue-400");
  });

  it("已耗时从 created_at 起算（5 秒前任务呈秒档、不含分/时档）", async () => {
    setup();
    const elapsed = await screen.findByTestId("task-elapsed");
    expect(elapsed.textContent).toMatch(/^\d+秒$/);
    expect(elapsed.textContent).not.toMatch(/[分时天]/);
  });

  it("无进度条：页面不含 ant-progress 元素", async () => {
    const { container } = setup();
    await screen.findByTestId("task-runtime-panel");
    expect(container.querySelector(".ant-progress")).toBeNull();
    expect(container.querySelector(".ant-progress-inner")).toBeNull();
  });
});

describe("Evolution D3 champion/snapshot 两源标注", () => {
  it("两源逐条标注来源（冠军 champion / 快照 snapshot·Gen N）", async () => {
    setup();
    const items = await screen.findAllByTestId("evolved-source-item");
    expect(items).toHaveLength(2);
    const sources = items.map((el) => el.getAttribute("data-source"));
    expect(sources).toContain("champion");
    expect(sources).toContain("snapshot");
    expect(screen.getByText("冠军 champion")).toBeInTheDocument();
    expect(screen.getByText(/快照 snapshot/)).toBeInTheDocument();
    expect(screen.getByText(/Gen 5/)).toBeInTheDocument();
  });

  it("两源计数概要：冠军 1 · 快照 1", async () => {
    setup();
    expect(await screen.findByTestId("evolved-sources-count")).toHaveTextContent("冠军 1 · 快照 1");
  });
});

describe("formatElapsed 纯函数", () => {
  it("分档取整：秒/分秒/时分/天时", () => {
    expect(formatElapsed(5)).toBe("5秒");
    expect(formatElapsed(90)).toBe("1分30秒");
    expect(formatElapsed(3600 + 120)).toBe("1时2分");
    expect(formatElapsed(2 * 86400 + 3 * 3600)).toBe("2天3时");
    expect(formatElapsed(-1)).toBe("0秒");
  });
});
