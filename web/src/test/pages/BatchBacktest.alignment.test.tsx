/**
 * 04 批量回测对齐批单测（alignment-batch D2）。
 * 结果表六列对齐（avg_total_return/avg_sharpe_ratio/worst_max_drawdown/
 * avg_fitness/qualified_count/total_periods）+fitness 排序键披露切换
 * （当前键文案明示+两键切换真实重排）。
 * 服务层全 mock（零外网红线）；stream hook 状态注入（done）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/services/strategies", () => ({
  getStrategies: vi.fn(),
  batchBacktestStream: vi.fn(),
  fetchBatchBacktestDetail: vi.fn(),
  verifyStrategiesStream: vi.fn(),
  getVerifySessions: vi.fn(),
  getSessionResults: vi.fn(),
}));

// stream 状态注入：useBatchBacktestStream 返回 done+summary（其余 hook 走真实层）
vi.mock("@/hooks/useStrategies", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useBatchBacktestStream: vi.fn(),
  };
});

import { BatchBacktest } from "@/pages/BatchBacktest";
import * as strat from "@/services/strategies";
import { useBatchBacktestStream } from "@/hooks/useStrategies";

/** A：fitness 高/夏普低；B：fitness 低/夏普高——两键排序结果相反 */
function mkSummaryItem(id: string, name: string, avgFitness: number, avgSharpe: number) {
  return {
    strategy_id: id,
    strategy_name: name,
    symbol: "BTCUSDT",
    timeframe: "4h",
    avg_total_return: id === "st-a" ? 0.12 : -0.05,
    avg_sharpe_ratio: avgSharpe,
    worst_max_drawdown: id === "st-a" ? -0.12 : -0.2,
    avg_fitness: avgFitness,
    qualified_count: id === "st-a" ? 2 : 1,
    total_periods: 2,
    per_period_results: [],
  };
}

const ITEM_A = mkSummaryItem("st-a", "策略甲", 5.0, 1.0);
const ITEM_B = mkSummaryItem("st-b", "策略乙", 2.0, 3.0);

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BatchBacktest />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(strat.getStrategies).mockResolvedValue({ items: [], total: 0 } as never);
  vi.mocked(strat.batchBacktestStream).mockImplementation(() => undefined);
  vi.mocked(strat.fetchBatchBacktestDetail).mockResolvedValue({} as never);
  vi.mocked(useBatchBacktestStream).mockReturnValue({
    status: "done",
    progress: { current: 2, total: 2 },
    currentGroup: "",
    rangeLabel: "",
    results: [],
    summary: [ITEM_A, ITEM_B],
    sessionId: null,
    error: null,
    start: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
  } as never);
});

/** 断言 first 的 DOM 位置在 second 之前 */
function expectBefore(first: Element, second: Element) {
  // Node.DOCUMENT_POSITION_FOLLOWING = 4：second 在 first 之后
  expect(first.compareDocumentPosition(second)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("BatchBacktest D2 六列对齐", () => {
  it("结果表六列表头齐备（六个聚合字段）", () => {
    setup();
    expect(screen.getByTestId("batch-head-avg_total_return")).toHaveTextContent("平均收益");
    expect(screen.getByTestId("batch-head-avg_sharpe_ratio")).toHaveTextContent("平均夏普");
    expect(screen.getByTestId("batch-head-worst_max_drawdown")).toHaveTextContent("最差回撤");
    expect(screen.getByTestId("batch-head-avg_fitness")).toHaveTextContent("平均Fitness");
    expect(screen.getByTestId("batch-head-qualified_count")).toHaveTextContent("达标数");
    expect(screen.getByTestId("batch-head-total_periods")).toHaveTextContent("区间数");
  });

  it("行值六列呈现：收益/夏普/回撤(abs 百分数)/Fitness/达标数/区间数", () => {
    setup();
    // 策略甲：+12.00% / 1.00 / 12.0% / 5.00 / 2 / 2
    expect(screen.getByText("+12.00%")).toBeInTheDocument();
    expect(screen.getByText("1.00")).toBeInTheDocument();
    expect(screen.getByText("12.0%")).toBeInTheDocument();
    expect(screen.getByText("5.00")).toBeInTheDocument();
    // 策略乙：-5.00% / 3.00 / 20.0%
    expect(screen.getByText("-5.00%")).toBeInTheDocument();
    expect(screen.getByText("3.00")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
  });
});

describe("BatchBacktest D2 fitness 排序键披露切换", () => {
  it("默认按平均 Fitness 降序且文案明示当前键", () => {
    setup();
    expect(screen.getByTestId("batch-sort-label")).toHaveTextContent("当前按 平均 Fitness 降序");
    expectBefore(screen.getByTestId("batch-result-row-st-a"), screen.getByTestId("batch-result-row-st-b"));
  });

  it("切换平均夏普后：文案跟随+行序真实重排（乙升首）", () => {
    setup();
    fireEvent.click(screen.getByTestId("batch-sort-btn-avg_sharpe_ratio"));
    expect(screen.getByTestId("batch-sort-label")).toHaveTextContent("当前按 平均夏普 降序");
    expectBefore(screen.getByTestId("batch-result-row-st-b"), screen.getByTestId("batch-result-row-st-a"));
  });

  it("切回平均 Fitness 恢复原序", () => {
    setup();
    fireEvent.click(screen.getByTestId("batch-sort-btn-avg_sharpe_ratio"));
    fireEvent.click(screen.getByTestId("batch-sort-btn-avg_fitness"));
    expect(screen.getByTestId("batch-sort-label")).toHaveTextContent("当前按 平均 Fitness 降序");
    expectBefore(screen.getByTestId("batch-result-row-st-a"), screen.getByTestId("batch-result-row-st-b"));
  });
});
