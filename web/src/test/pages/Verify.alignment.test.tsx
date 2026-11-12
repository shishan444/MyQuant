/**
 * 03 验证记录对齐批单测（页面已重构为浏览骨架+发起器折叠区——本批锚定折叠区内历史会话与三门槛组件行为）（alignment-batch D1）。
 * 三门槛点行三态（true=绿/false=红/null=灰）+星级机械换算（0-3）+
 * 三态汇总计数（pass/fail/inconclusive）+B5 概要形制（星级+门槛+关键指标）。
 * 服务层全 mock（零外网红线）。
 * P-1 容器满宽不可达判定（upgrade-vr-responsive-layout 阶段四闸门锚）：
 * 本页撤 max-w-[1400px]/mx-auto 两 class 无单测 class 锚——jsdom class 负
 * 断言对「满宽几何」保护力弱（class 存在与否≠像素满宽），几何判定归
 * e2e main-paths 01 页打开+三档视口截图矩阵（tasks.md 2.2 实施证据落档）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/services/strategies", () => ({
  getStrategies: vi.fn(),
  verifyStrategiesStream: vi.fn(),
  getVerifySessions: vi.fn(),
  getSessionResults: vi.fn(),
}));

import { Verify } from "@/pages/Verify";
import * as strat from "@/services/strategies";
import {
  classifyThresholds,
  summarizeThresholds,
  formatDrawdown,
} from "@/components/verify/thresholdsUtils";

const STRATEGY = {
  strategy_id: "st-1",
  name: "策略一",
  dna: null,
  symbol: "BTCUSDT",
  timeframe: "4h",
  source: "lab",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const SESSION = {
  session_id: "s1",
  status: "completed",
  strategy_ids: JSON.stringify(["st-1"]),
  data_ranges: JSON.stringify([{ start: "2026-01-01", end: "2026-02-01" }]),
  init_cash: 100000,
  fee: 0.001,
  slippage: 0.0005,
  summary_json: null,
  total_results: 3,
  total_strategies: 1,
  error_message: null,
  created_at: "2026-10-05T10:00:00Z",
  completed_at: "2026-10-05T10:05:00Z",
};

/** 行扩展字段直构（后端 VerifyHistoryItem 已声明 thresholds/star/total_trades） */
function mkRow(overrides: Record<string, unknown>) {
  return {
    result_id: "r0",
    strategy_id: "st-1",
    strategy_name: "策略一",
    symbol: "BTCUSDT",
    timeframe: "4h",
    data_start: "2026-01-01",
    data_end: "2026-02-01",
    total_return: 0.1,
    sharpe_ratio: 1.5,
    max_drawdown: -0.08,
    total_trades: 10,
    fitness: 1.2,
    qualified: 1,
    thresholds: { return_positive: true, drawdown_ok: true, trades_enough: true },
    star: 3,
    created_at: "2026-10-05T10:05:00Z",
    ...overrides,
  };
}

const ROWS = [
  // 全过 → pass / star 3
  mkRow({ result_id: "r1", thresholds: { return_positive: true, drawdown_ok: true, trades_enough: true }, star: 3 }),
  // 回撤门槛 false → fail（false 优先于缺失归档）/ star 2
  mkRow({ result_id: "r2", thresholds: { return_positive: true, drawdown_ok: false, trades_enough: true }, star: 2, max_drawdown: -0.3 }),
  // 指标缺失 null（无 false）→ inconclusive / star 1
  mkRow({ result_id: "r3", thresholds: { return_positive: null, drawdown_ok: true, trades_enough: null }, star: 1, total_return: 0 }),
];

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Verify />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(strat.getStrategies).mockResolvedValue({ items: [STRATEGY as never], total: 1 } as never);
  vi.mocked(strat.verifyStrategiesStream).mockImplementation(() => undefined);
  vi.mocked(strat.getVerifySessions).mockResolvedValue({ items: [SESSION as never], total: 1 } as never);
  vi.mocked(strat.getSessionResults).mockResolvedValue({ items: ROWS as never, total: 3 } as never);
});

async function openHistoryResults() {
  setup();
  // fe-validation-records-page：发起器+历史迁入折叠区（R5+R7-b）——两步进入
    fireEvent.click(screen.getByText("发起器 · 历史会话"));
    fireEvent.click(screen.getByText("历史会话"));
  fireEvent.click(await screen.findByText("2026-10-05 10:00"));
  await screen.findByTestId("session-results-table");
}

describe("Verify D1 三门槛点行/星级/三态汇总", () => {
  it("会话结果表呈概要形制：星级+门槛+关键指标列齐备", async () => {
    await openHistoryResults();
    expect(screen.getByText("星级")).toBeInTheDocument();
    expect(screen.getByText("门槛")).toBeInTheDocument();
    expect(screen.getByText("年化")).toBeInTheDocument();
    expect(screen.getByText("回撤")).toBeInTheDocument();
    expect(screen.getByText("交易")).toBeInTheDocument();
  });

  it("三门槛点行三态：全过=pass、含 false=fail、缺失=null 灰", async () => {
    await openHistoryResults();
    // 渲染序=ROWS 序：r1 全 pass、r2 drawdown fail、r3 两键 missing
    const returnDots = screen.getAllByTestId("threshold-dot-return_positive");
    expect(returnDots[0]).toHaveAttribute("data-state", "pass");
    expect(returnDots[2]).toHaveAttribute("data-state", "missing");

    const ddDots = screen.getAllByTestId("threshold-dot-drawdown_ok");
    expect(ddDots[0]).toHaveAttribute("data-state", "pass");
    expect(ddDots[1]).toHaveAttribute("data-state", "fail");

    const tradesDots = screen.getAllByTestId("threshold-dot-trades_enough");
    expect(tradesDots[2]).toHaveAttribute("data-state", "missing");
  });

  it("星级=star 机械换算（亮星重复 star 次）", async () => {
    await openHistoryResults();
    const stars = screen.getAllByTestId("verify-star");
    expect(stars).toHaveLength(3);
    // r1: 亮 3 / r2: 亮 2 / r3: 亮 1
    const litPerRow = screen.getAllByTestId("verify-star").map((s) =>
      s.querySelectorAll('[data-testid="verify-star-lit"]').length
    );
    expect(litPerRow).toEqual([3, 2, 1]);
  });

  it("三态汇总计数：达标 1 · 未达标 1 · 缺失 1", async () => {
    await openHistoryResults();
    expect(screen.getByTestId("thresholds-summary-pass")).toHaveTextContent("达标 1");
    expect(screen.getByTestId("thresholds-summary-fail")).toHaveTextContent("未达标 1");
    expect(screen.getByTestId("thresholds-summary-inconclusive")).toHaveTextContent("缺失 1");
  });

  it("回撤展示取 abs+百分数（-0.08 → 8.0%）", async () => {
    await openHistoryResults();
    expect(screen.getAllByText("8.0%").length).toBeGreaterThan(0);
  });
});

describe("VerifyThresholds 纯函数", () => {
  it("classifyThresholds：false 优先于缺失；无 false 有 null=inconclusive；全 true=pass", () => {
    expect(classifyThresholds({ return_positive: true, drawdown_ok: false, trades_enough: null })).toBe("fail");
    expect(classifyThresholds({ return_positive: null, drawdown_ok: true, trades_enough: null })).toBe("inconclusive");
    expect(classifyThresholds({ return_positive: true, drawdown_ok: true, trades_enough: true })).toBe("pass");
    expect(classifyThresholds(null)).toBe("inconclusive");
  });

  it("summarizeThresholds 三档计数", () => {
    const counts = summarizeThresholds(ROWS.map((r) => ({ thresholds: r.thresholds })));
    expect(counts).toEqual({ pass: 1, fail: 1, inconclusive: 1 });
  });

  it("formatDrawdown 负值小数→abs 百分数", () => {
    expect(formatDrawdown(-0.08)).toBe("8.0%");
    expect(formatDrawdown(0.25)).toBe("25.0%");
  });
});

describe("CARD-01 结果卡概要取较深回撤", () => {
  it("两区间 -5%/-20% → AggregateMetrics 概要行显示 -20.0%（Math.min 负值口径；旧 Math.max 反转会取 -5.0%）", async () => {
    let captured: { onComplete: (data: unknown) => void } | null = null;
    vi.mocked(strat.verifyStrategiesStream).mockImplementation(
      ((_payload: unknown, callbacks: { onComplete: (data: unknown) => void }) => {
        captured = callbacks;
      }) as never,
    );

    setup();
    // fe-validation-records-page：发起器默认折叠——先开折叠区再进入发起器
    fireEvent.click(screen.getByText("发起器 · 历史会话"));
    // 等策略列表加载（按钮计数 0→1），再点近3月预设填区间、点开始验证
    // 触发 stream.start（捕获 callbacks）
    await screen.findByText(/开始验证 \(1\)/);
    fireEvent.click(screen.getByText("近3月"));
    fireEvent.click(screen.getByText(/开始验证 \(1\)/));
    expect(captured).not.toBeNull();

    await act(async () => {
      captured!.onComplete({
        session_id: "s-card",
        summary: [{
          strategy_id: "st-1",
          strategy_name: "策略一",
          comprehensive_score: 8.4,
          avg_fitness: 7.0,
          qualified_count: 2,
          total_periods: 2,
          per_period_metrics: [
            { data_start: "2026-01-01", data_end: "2026-02-01", fitness: 7,
              qualified: true, total_return: 0.1, sharpe_ratio: 1.5,
              max_drawdown: -0.05 },
            { data_start: "2026-02-02", data_end: "2026-03-01", fitness: 7,
              qualified: true, total_return: 0.1, sharpe_ratio: 1.5,
              max_drawdown: -0.2 },
          ],
        }],
        results: [],
      });
    });

    expect(screen.getByText("回撤 -20.0%")).toBeInTheDocument();
    expect(screen.queryByText("回撤 -5.0%")).not.toBeInTheDocument();
  });
});
