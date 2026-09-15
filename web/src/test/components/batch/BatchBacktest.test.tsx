/**
 * BatchBacktest 页测试（G8 AntD 迁移，新建）。
 * 断言语义=用户可见行为：策略选择表（勾选联动计数）/筛选/日期配置/LoadDetail
 * 详情展开链路（断言语义保持——fetchBatchBacktestDetail 调用与详情呈现）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { BatchBacktest } from "@/pages/BatchBacktest";
import { antdThemeConfig } from "@/theme/antdTheme";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { BatchBacktestSummaryItem, Strategy } from "@/types/api";

vi.mock("@/hooks/useStrategies", () => ({
  useStrategies: vi.fn(),
  useBatchBacktestStream: vi.fn(),
}));

vi.mock("@/services/strategies", () => ({
  fetchBatchBacktestDetail: vi.fn(),
}));

import { useStrategies, useBatchBacktestStream } from "@/hooks/useStrategies";
import { fetchBatchBacktestDetail } from "@/services/strategies";

const doneSummary: BatchBacktestSummaryItem[] = [
  {
    strategy_id: "s1",
    strategy_name: "Alpha",
    symbol: "BTCUSDT",
    timeframe: "4h",
    avg_total_return: 0.12,
    avg_sharpe_ratio: 1.5,
    worst_max_drawdown: -0.08,
    avg_fitness: 3.2,
    qualified_count: 2,
    total_periods: 2,
    per_period_results: [
      {
        result_id: "r1",
        strategy_id: "s1",
        strategy_name: "Alpha",
        symbol: "BTCUSDT",
        timeframe: "4h",
        data_start: "2025-01-01",
        data_end: "2025-03-01",
        total_return: 0.1,
        sharpe_ratio: 1.4,
        max_drawdown: -0.06,
        win_rate: 0.55,
        total_trades: 40,
        profit_factor: 1.6,
        fitness: 3.0,
        qualified: true,
        liquidated: false,
        total_funding_cost: 0,
      },
    ],
  },
];

function makeStrategy(id: string, name: string): Strategy {
  return {
    strategy_id: id,
    name,
    symbol: "BTCUSDT",
    timeframe: "4h",
    source: "lab",
    generation: 0,
    verify_count: 0,
    verify_star: 4,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
  };
}

function setup(opts: { strategies?: Strategy[]; streamStatus?: string; summary?: unknown[] } = {}) {
  const strategies = opts.strategies ?? [makeStrategy("s1", "Alpha"), makeStrategy("s2", "Beta")];
  const streamStatus = opts.streamStatus ?? "idle";
  vi.mocked(useStrategies).mockReturnValue({
    queryKey: ["strategies"],
    queryFn: () => Promise.resolve({ items: strategies, total: strategies.length }),
    isLoading: false,
  } as never);
  vi.mocked(useBatchBacktestStream).mockReturnValue({
    status: streamStatus,
    progress: { current: 0, total: 0 },
    currentGroup: "",
    rangeLabel: "",
    results: [],
    summary: opts.summary ?? [],
    sessionId: null,
    error: null,
    start: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
  } as never);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ConfigProvider theme={antdThemeConfig} locale={zhCN}>
          <AntdApp>
            <BatchBacktest />
          </AntdApp>
        </ConfigProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user };
}

describe("BatchBacktest（AntD）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("策略选择：勾选行联动已选计数", async () => {
    const { user } = setup();
    await screen.findByText("Alpha");
    expect(screen.getByText(/已选 0 \/ 2/)).toBeInTheDocument();
    // 行点击=选择策略（与 checkbox 等价路径）
    await user.click(screen.getByText("Alpha"));
    await waitFor(() => expect(screen.getByText(/已选 1 \/ 2/)).toBeInTheDocument());
  });

  it("搜索过滤：输入不存在的词清空列表提示", async () => {
    const { user } = setup();
    await screen.findByText("Alpha");
    await user.type(screen.getByPlaceholderText("搜索策略..."), "zzz");
    expect(await screen.findByText("无匹配策略")).toBeInTheDocument();
  });

  it("空策略库：引导提示", async () => {
    setup({ strategies: [] });
    expect(await screen.findByText("策略库为空，请先到策略库创建策略")).toBeInTheDocument();
  });

  it("结果态：展开策略行→点详情触发 LoadDetail 并呈现详情内容", async () => {
    vi.mocked(fetchBatchBacktestDetail).mockResolvedValue({
      result_id: "r1",
      strategy_id: "s1",
      symbol: "BTCUSDT",
      timeframe: "4h",
      init_cash: 100000,
      fee: 0.001,
      slippage: 0.0005,
      total_return: 0.1,
      sharpe_ratio: 1.4,
      max_drawdown: -0.06,
      win_rate: 0.55,
      total_trades: 40,
      total_score: 70,
      fitness: 3,
      qualified: true,
      template_name: "explorer",
      run_source: "batch",
      total_funding_cost: 0,
      liquidated: false,
      equity_curve: [],
    } as never);
    const { user } = setup({ streamStatus: "done", summary: doneSummary as never });
    // 结果行：策略名 Alpha
    // done 态未收起配置区：Alpha 同时出现在选择表与结果行，取结果行（按钮内）
    const alphas = await screen.findAllByText("Alpha");
    const row = alphas.find((el) => el.closest("button")) ?? alphas[alphas.length - 1];
    await user.click(row.closest("button") ?? row);
    // 展开后周期行出现「详情」按钮
    const detailBtn = await screen.findByRole("button", { name: /详\s*情/ });
    await user.click(detailBtn);
    await waitFor(() => expect(fetchBatchBacktestDetail).toHaveBeenCalledWith("r1"));
    // 详情内容（资金曲线标题）
    await waitFor(() => expect(screen.getByText("资金曲线")).toBeInTheDocument());
  });
});
