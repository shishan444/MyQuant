/**
 * 06 策略库对齐批单测（alignment-batch D4）。
 * name=NULL 兜底（完整 strategy_id）/六态 chips（计数+筛选+draft 补拉合并）/
 * 四段履历聚合（诞生/验证/模拟/审计）/DNA 五段呈现。
 * 服务层全 mock（零外网红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/services/strategies", () => ({
  getStrategies: vi.fn(),
  getStrategiesByStatus: vi.fn(),
  getVerifyHistory: vi.fn(),
  deleteStrategy: vi.fn(),
  updateStrategy: vi.fn(),
}));

vi.mock("@/services/trading", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listTradingTasks: vi.fn(),
}));

import { Strategies } from "@/pages/Strategies";
import * as strat from "@/services/strategies";
import * as trading from "@/services/trading";

const DNA = {
  signal_genes: [
    { indicator: "ema", params: { period: 20 }, role: "entry_trigger", condition: { type: "cross_above", threshold: 0 } },
  ],
  logic_genes: { entry_logic: "AND", exit_logic: "OR" },
  risk_genes: { direction: "long", leverage: 2, sl_mode: "pct", stop_loss: 0.05, take_profit: 0.1, position_size: 0.3 },
  execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
  layers: null,
};

function mkStrategy(id: string, name: string | null, status: string) {
  return {
    strategy_id: id, name, dna: DNA, symbol: "BTCUSDT", timeframe: "4h",
    source: "lab", source_task_id: null, gene_signature: null, status,
    best_score: null, best_fitness: 1.1, qualified: null, metrics: null,
    generation: 0, parent_ids: null, tags: null, notes: null,
    verify_count: 0, verify_avg_score: null, verify_best_score: null,
    last_verified_at: null, verify_star: null,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  };
}

const ACTIVE = mkStrategy("st-active-0001-full-id", null, "active");
const PAPER = mkStrategy("st-paper-0002", "模拟策略A", "paper");
const DRAFT = mkStrategy("st-draft-0003", "草稿策略B", "draft");

const VERIFY_HISTORY = {
  items: [
    {
      result_id: "r1", strategy_id: "st-active-0001-full-id", strategy_name: null,
      symbol: "BTCUSDT", timeframe: "4h", data_start: "2026-01-01", data_end: "2026-02-01",
      total_return: 0.12, sharpe_ratio: 1.5, max_drawdown: -0.1, total_trades: 30,
      fitness: 1.2, qualified: 1, thresholds: null, star: 2, created_at: "2026-10-05T00:00:00Z",
    },
  ],
  total: 1,
};

const PAPER_TASKS = {
  tasks: [
    { task_id: "pt1", strategy_id: "st-active-0001-full-id", status: "completed", created_at: "2026-10-06T00:00:00Z" },
  ],
  total: 1,
};

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Strategies />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // 主视图（status 缺省排除 draft）+draft 补拉——模拟两次请求合并语义
  vi.mocked(strat.getStrategies).mockResolvedValue({ items: [ACTIVE, PAPER], total: 2 } as never);
  vi.mocked(strat.getStrategiesByStatus).mockResolvedValue({ items: [DRAFT], total: 1 } as never);
  vi.mocked(strat.getVerifyHistory).mockResolvedValue(VERIFY_HISTORY as never);
  vi.mocked(trading.listTradingTasks).mockResolvedValue(PAPER_TASKS as never);
});

describe("Strategies（06 策略库·对齐批）", () => {
  it("星级位页级锚（t3r4 G3）：4 星行渲染五槽 svg，null 行无星级节点", async () => {
    // 4 星变体：复用 ACTIVE 骨架覆写 verify_star
    const starred = { ...ACTIVE, strategy_id: "st-star-4", verify_star: 4 };
    const nullStar = { ...ACTIVE, strategy_id: "st-star-null", verify_star: null };
    vi.mocked(strat.getStrategies).mockResolvedValue({
      items: [starred, nullStar], total: 2,
    } as never);
    setup();
    // draft 补拉行也在场（DRAFT mock 仍生效）——按 strategy_id 文本定位目标行
    await screen.findAllByTestId("strategy-name");
    const starRow = screen.getByText("st-star-4").closest("tr");
    const nullRow = screen.getByText("st-star-null").closest("tr");
    // 4 星行：StarRating 五槽（槽特征=fill-current/opacity-30——行内另有
    // 操作钮 lucide svg，按槽特征计数；旧 ★n 数字形无此特征锚兼防回退）
    const starSlots = starRow!.querySelectorAll(
      'svg [class*="fill-current"], svg[class*="fill-current"], svg [class*="opacity-30"], svg[class*="opacity-30"]',
    );
    expect(starSlots.length).toBe(5);
    // null 行：星级位不渲染（:599 条件 False 侧——无槽特征 svg）
    const nullSlots = nullRow!.querySelectorAll(
      'svg [class*="fill-current"], svg[class*="fill-current"], svg [class*="opacity-30"], svg[class*="opacity-30"]',
    );
    expect(nullSlots.length).toBe(0);
  });

  it("name=NULL 兜底：行名显示完整 strategy_id（不截断不造名）", async () => {
    setup();
    const names = await screen.findAllByTestId("strategy-name");
    expect(names[0]).toHaveTextContent("st-active-0001-full-id");
  });

  it("六态 chips：全部=主视图+draft 合并计数，六态 chip 带各自计数", async () => {
    setup();
    expect(await screen.findByTestId("status-chips")).toBeInTheDocument();
    // 全部=3（主视图 2+draft 补拉 1）
    expect(screen.getByTestId("status-chip-all")).toHaveTextContent("3");
    expect(screen.getByTestId("status-chip-draft")).toHaveTextContent("1");
    expect(screen.getByTestId("status-chip-active")).toHaveTextContent("1");
    expect(screen.getByTestId("status-chip-paper")).toHaveTextContent("1");
    // 其余态计数 0 也呈现（六态值域完整）
    expect(screen.getByTestId("status-chip-candidate")).toHaveTextContent("0");
    expect(screen.getByTestId("status-chip-live")).toHaveTextContent("0");
    expect(screen.getByTestId("status-chip-archived")).toHaveTextContent("0");
  });

  it("draft 补拉合并：草稿策略行可见（缺省视图本不含 draft）", async () => {
    setup();
    expect(await screen.findByText("草稿策略B")).toBeInTheDocument();
  });

  it("六态筛选：点击 paper chip 仅剩模拟行", async () => {
    setup();
    await screen.findByText("草稿策略B");
    fireEvent.click(screen.getByTestId("status-chip-paper"));
    expect(screen.getByText("模拟策略A")).toBeInTheDocument();
    // framer-motion exit 动画：行移除有延迟，waitFor 等待消失
    await waitFor(() => expect(screen.queryByText("草稿策略B")).toBeNull());
    // name 兜底行（active）也被筛除——其兜底 id 文本不再呈现（paper 行仍在属正常）
    await waitFor(() => expect(screen.queryByText("st-active-0001-full-id")).toBeNull());
  });

  it("行内六态徽标呈现（status badge）", async () => {
    setup();
    expect(await screen.findByTestId("strategy-status-active")).toHaveTextContent("可消费");
    expect(screen.getByTestId("strategy-status-paper")).toHaveTextContent("模拟在验");
    expect(screen.getByTestId("strategy-status-draft")).toHaveTextContent("草稿");
  });

  it("四段履历聚合：展开行呈现诞生/验证/模拟/审计四段（审计如实披露无端点）", async () => {
    setup();
    await screen.findByText("草稿策略B");
    fireEvent.click(screen.getAllByTestId("strategy-name")[0]);
    expect(await screen.findByTestId("career-record")).toBeInTheDocument();
    // 诞生段：日期+来源
    expect(screen.getByTestId("career-segment-birth")).toBeInTheDocument();
    // 验证段：1 条记录+最近日期+星级
    const verifySeg = screen.getByTestId("career-segment-verify");
    expect(verifySeg).toHaveTextContent("1 条");
    expect(verifySeg).toHaveTextContent("★2");
    // 模拟段：1 个任务+最近状态（completed→已完结）
    expect(screen.getByTestId("career-segment-paper")).toHaveTextContent("1 个任务");
    expect(screen.getByTestId("career-segment-paper")).toHaveTextContent("已完结");
    // 审计段：无查询端点，如实披露
    expect(screen.getByTestId("career-segment-audit")).toHaveTextContent("端点未开放");
  });

  it("DNA 五段呈现：信号/逻辑/风险/执行/多周期层", async () => {
    setup();
    await screen.findByText("草稿策略B");
    fireEvent.click(screen.getAllByTestId("strategy-name")[0]);
    const dna = await screen.findByTestId("dna-five-segments");
    expect(dna).toHaveTextContent("信号基因（1）");
    expect(dna).toHaveTextContent("逻辑基因");
    expect(dna.textContent).toContain("AND 入场 · OR 出场");
    expect(dna).toHaveTextContent("风险基因");
    expect(dna.textContent).toContain("做多 · 2x · 百分比档");
    expect(dna).toHaveTextContent("执行基因");
    expect(dna.textContent).toContain("BTCUSDT · 4h");
    expect(dna).toHaveTextContent("多周期层（0）");
    expect(dna.textContent).toContain("单层结构（无多周期层）");
  });

  it("CARD-06 direction 缺失：风险段方向位占位「—」、信息面板方向行占位「--」，不兜「做多」", async () => {
    // direction undefined 策略（其余字段齐备——占位只发生在方向位）
    const stg = mkStrategy("st-nodir-0001", "无方向策略", "active");
    stg.dna = {
      ...DNA,
      risk_genes: { ...DNA.risk_genes, direction: undefined },
    } as unknown as typeof DNA;
    vi.mocked(strat.getStrategies).mockResolvedValue({ items: [stg], total: 1 } as never);
    setup();
    fireEvent.click((await screen.findAllByTestId("strategy-name"))[0]);
    // 风险基因段（:313 形制）：— · 2x · 百分比档（占位符后拼接保留）
    const dna = await screen.findByTestId("dna-five-segments");
    expect(dna.textContent).toContain("— · 2x · 百分比档");
    // 信息面板方向行（:429 形制）：占位「--」；整页无「做多」兜底文案
    expect(screen.getAllByText("--").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/做多/)).not.toBeInTheDocument();
  });
});
