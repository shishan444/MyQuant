/**
 * 08 实盘交易·目标态预览页测试（live-trading-preview）。
 * 锚：八区块齐备/demo 域标注与术语/强平价「—」/风控四条真实值/
 * M1 对话框开合与门槛清单/demo 空态如实。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LiveTrading } from "@/pages/LiveTrading";
import * as svc from "@/services/liveTrading";

vi.mock("@/services/liveTrading", () => ({
  listLiveTasks: vi.fn(),
}));

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LiveTrading />
    </QueryClientProvider>,
  );
}

describe("08 实盘预览页（demo-only）", () => {
  beforeEach(() => {
    vi.mocked(svc.listLiveTasks).mockResolvedValue([]);
  });

  it("页级 demo 域标注：现状披露+目标态锚声明，术语 demo 非 testnet", async () => {
    mount();
    const banner = await screen.findByTestId("live-demo-banner");
    expect(banner.textContent).toContain("demo 域预览");
    expect(banner.textContent).toContain("目标态预览");
    expect(banner.textContent).toContain("REQ-2-01~13");
    // 术语约束：全页禁 testnet（编号索引 v2 修订⑦）
    expect(document.body.textContent).not.toContain("testnet");
  });

  it("八区块齐备（B1/B2/B3/B4/B5/B6r/B7 + 横幅）", async () => {
    mount();
    await screen.findByTestId("live-b1");
    for (const id of ["live-b2", "live-b3", "live-b4", "live-b5", "live-b6", "live-b7"]) {
      expect(document.querySelector(`[data-testid="${id}"]`)).toBeTruthy();
    }
    // B6r 决策快照表 4 行含依据列
    expect(document.querySelectorAll("[data-testid='live-b6-table'] tbody tr")).toHaveLength(4);
  });

  it("B5 风控四条真实值 500U/5s/1000U/50% 与安全三件", async () => {
    mount();
    await screen.findByTestId("live-b5");
    for (const v of ["500U", "5s", "1000U", "50%"]) {
      expect(document.querySelector(`[data-testid='live-risk-${v}']`)?.textContent).toContain(v);
    }
    expect(screen.getByText("操作审计")).toBeInTheDocument();
  });

  it("强平价显「—」（demo 域无强平链）+B4 预览样例标注", async () => {
    mount();
    await screen.findByTestId("live-b3");
    // 空任务态 B3 未选中——强平价断言在选中任务用例；此处锚 B4 标注
    expect(screen.getByText("目标态预览样例")).toBeInTheDocument();
  });

  it("选中任务时仓位条呈现且强平价「—」；B1 三态渲染", async () => {
    vi.mocked(svc.listLiveTasks).mockResolvedValue([
      {
        task_id: "lt-1", symbol: "BTCUSDT", timeframe: "4h", status: "running",
        direction: "mixed", position_side: "LONG", position_qty: 0.01, position_entry: 62000,
        balance: 200, equity: 201.5, unrealized_pnl: 1.5, total_trades: 3, total_pnl: 1.5,
        created_at: "2026-11-01T00:00:00Z",
      },
      {
        task_id: "lt-2", symbol: "ETHUSDT", timeframe: "1h", status: "stopped",
        direction: "mixed", position_side: "", position_qty: 0, position_entry: 0,
        balance: 200, equity: 200, unrealized_pnl: 0, total_trades: 0, total_pnl: 0,
        created_at: "2026-11-01T00:00:00Z",
      },
    ]);
    mount();
    // B1 两行渲染（running 进行中/stopped 规则停机）
    await screen.findByTestId("live-task-lt-1");
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("规则停机")).toBeInTheDocument();
    // 默认选中首任务——强平价「—」
    const dash = await screen.findByTestId("live-liquidation-dash");
    expect(dash.textContent).toBe("—");
    // live 徽标
    expect(screen.getByTestId("live-badge").textContent).toContain("live");
  });

  it("空任务态如实（不造样例任务）", async () => {
    mount();
    expect(await screen.findByText(/无 demo 域任务/)).toBeInTheDocument();
    expect(document.querySelector("[data-testid='live-task-']")).toBeNull();
  });

  it("M1 创建对话框：开合+四重门槛清单+200U+裁决门+demo 标注", async () => {
    mount();
    await screen.findByTestId("live-b1");
    fireEvent.click(screen.getByTestId("live-m1-trigger"));
    expect(await screen.findByTestId("live-m1-modal")).toBeInTheDocument();
    for (let i = 0; i < 4; i++) {
      expect(document.querySelector(`[data-testid='live-m1-gate-${i}']`)).toBeTruthy();
    }
    expect(document.querySelector("[data-testid='live-m1-modal']")?.textContent).toContain("200U");
    expect(document.querySelector("[data-testid='live-m1-modal']")?.textContent).toContain("人工裁决门");
    expect(document.querySelector("[data-testid='live-m1-submit']")?.textContent).toContain("demo");
  });
});
