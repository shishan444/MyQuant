/**
 * 08 实盘交易·目标态预览页测试（live-trading-preview）。
 * 锚：八区块齐备/demo 域标注与术语（demo 非测试网/testnet 两形制）/
 * 强平价「—」/风控四条真实值/B1 四态值域/B2 人工停止与全冻结锁标/
 * B4 双确认按钮/M1 对话框开合与门槛清单/demo 空态如实。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LiveTrading } from "@/pages/LiveTrading";
import * as svc from "@/services/liveTrading";
import { toast } from "sonner";

vi.mock("@/services/liveTrading", () => ({
  listLiveTasks: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { info: vi.fn() },
}));

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LiveTrading />
    </QueryClientProvider>,
  );
}

/** 构造 demo 域任务（壳端点契约形制） */
function makeTask(over: Partial<svc.LiveTradingTask>): svc.LiveTradingTask {
  return {
    task_id: "lt-x", symbol: "BTCUSDT", timeframe: "4h", status: "running",
    direction: "mixed", position_side: "LONG", position_qty: 0.01, position_entry: 62000,
    balance: 200, equity: 201.5, unrealized_pnl: 1.5, total_trades: 3, total_pnl: 1.5,
    created_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

describe("08 实盘预览页（demo-only）", () => {
  beforeEach(() => {
    vi.mocked(svc.listLiveTasks).mockResolvedValue([]);
    vi.mocked(toast.info).mockClear();
  });

  it("页级 demo 域标注：现状披露+目标态锚声明，术语 demo 非测试网/testnet（两形制负断言）", async () => {
    mount();
    const banner = await screen.findByTestId("live-demo-banner");
    expect(banner.textContent).toContain("demo 域预览");
    expect(banner.textContent).toContain("目标态预览");
    expect(banner.textContent).toContain("REQ-2-01~13");
    // 术语约束：全页禁 testnet 与中文「测试网」（编号索引 v2 修订⑦）
    expect(document.body.textContent).not.toContain("testnet");
    expect(document.body.textContent).not.toContain("测试网");
  });

  it("八区块齐备：B1-B7 testid + 页级横幅（M1 对话框在专属用例锚）", async () => {
    mount();
    await screen.findByTestId("live-b1");
    for (const id of ["live-b2", "live-b3", "live-b4", "live-b5", "live-b6", "live-b7"]) {
      expect(document.querySelector(`[data-testid="${id}"]`)).toBeTruthy();
    }
    // B6r 决策快照表 4 行
    expect(document.querySelectorAll("[data-testid='live-b6-table'] tbody tr")).toHaveLength(4);
  });

  it("B1 四态值域渲染：进行中/暂停待确认/规则停机/出错", async () => {
    vi.mocked(svc.listLiveTasks).mockResolvedValue([
      makeTask({ task_id: "lt-run", status: "running" }),
      makeTask({ task_id: "lt-pend", status: "pending" }),
      makeTask({ task_id: "lt-stop", status: "stopped" }),
      makeTask({ task_id: "lt-err", status: "error" }),
    ]);
    mount();
    await screen.findByTestId("live-task-lt-err");
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("暂停待确认")).toBeInTheDocument();
    expect(screen.getByText("规则停机")).toBeInTheDocument();
    expect(screen.getByText("出错")).toBeInTheDocument();
  });

  it("B2 选中任务头卡：live 徽标+人工停止按钮（预览标注）+全冻结锁标；停止仅本地提示不发送指令", async () => {
    vi.mocked(svc.listLiveTasks).mockResolvedValue([
      makeTask({ task_id: "lt-1", symbol: "BTCUSDT", timeframe: "4h", status: "running" }),
    ]);
    mount();
    await screen.findByTestId("live-task-lt-1");
    expect(screen.getByTestId("live-badge").textContent).toContain("live");
    expect(screen.getByLabelText("全冻结锁标")).toBeInTheDocument();
    // CARD-05：按钮文本内嵌「（预览）」——对齐 B1「创建实盘任务（预览）」先例
    expect(screen.getByTestId("live-stop-btn").textContent).toContain("（预览）");
    fireEvent.click(screen.getByTestId("live-stop-btn"));
    expect(toast.info).toHaveBeenCalledWith(
      "demo 域预览——人工停止不发送指令（实盘链未建设）",
    );
  });

  it("B5 风控四条真实值 1000U/5s/500U/50% 与 label 配对（risk_guard.py:37-40 工程现状权威源）", async () => {
    mount();
    await screen.findByTestId("live-b5");
    // 配对断言：testid 数值与 label 须同 chip——存在性断言捕获不了
    // label-数值配对翻转（CARD-01：数值集相同仅配对错位时旧断言恒绿）。
    const pairs: Array<[testid: string, label: string, note?: string]> = [
      ["live-risk-1000U", "单笔名义上限"],
      ["live-risk-5s", "最小下单间隔"],
      ["live-risk-500U", "日亏损熔断", "触及停止开新仓"],
      ["live-risk-50%", "保证金占用上限", "占权益"],
    ];
    for (const [testid, label, note] of pairs) {
      const chip = document.querySelector(`[data-testid='${testid}']`);
      expect(chip, testid).toBeDefined();
      expect(chip?.textContent).toContain(label);
      if (note) expect(chip?.textContent).toContain(note);
    }
    // 安全三件（CARD-02：REQ-2-14 目标态三件——PRD :166；工程现状未建，mainnet 裁决门前置）
    expect(screen.getByText("总回撤≥20% 全停")).toBeInTheDocument();
    expect(screen.getByText("篮子合计敞口上限")).toBeInTheDocument();
    expect(screen.getByText("学费预算·累计净亏损上限")).toBeInTheDocument();
    // B5 分处标注（CARD-02）：风控行=工程现状、安全行=目标态，两组不混行
    const b5 = screen.getByTestId("live-b5");
    expect(b5.textContent).toContain("【工程现状】");
    expect(b5.textContent).toContain("【目标态】");
  });

  it("B4 外部干预：预览样例标注+确认继续/确认终止双按钮（spec B4 区块定义）", async () => {
    mount();
    await screen.findByTestId("live-b4");
    expect(screen.getByText("目标态预览样例")).toBeInTheDocument();
    expect(screen.getByText("确认继续")).toBeInTheDocument();
    expect(screen.getByText("确认终止")).toBeInTheDocument();
  });

  it("B6 决策快照：demo 样例标注「目标态预览样例」+依据列（spec Scenario 4——B6 与 B4 同词形）", async () => {
    mount();
    const b6 = await screen.findByTestId("live-b6");
    // spec THEN：无真实事件流的数据区呈现 demo 样例并标注「目标态预览样例」
    expect(b6.textContent).toContain("目标态预览样例");
    // B6r 依据列（决策因果依据列）表头在位
    expect(b6.querySelectorAll("thead th")[2].textContent).toContain("依据");
  });

  it("选中任务时仓位条呈现且强平价「—」（demo 域无强平链）", async () => {
    vi.mocked(svc.listLiveTasks).mockResolvedValue([
      makeTask({ task_id: "lt-1", status: "running" }),
      makeTask({ task_id: "lt-2", symbol: "ETHUSDT", timeframe: "1h", status: "stopped",
        position_side: "", position_qty: 0, position_entry: 0,
        balance: 200, equity: 200, unrealized_pnl: 0, total_trades: 0, total_pnl: 0 }),
    ]);
    mount();
    await screen.findByTestId("live-task-lt-1");
    // 默认选中首任务——强平价「—」
    const dash = await screen.findByTestId("live-liquidation-dash");
    expect(dash.textContent).toBe("—");
  });

  it("空任务态如实（不造样例任务）", async () => {
    mount();
    expect(await screen.findByText(/无 demo 域任务/)).toBeInTheDocument();
    expect(document.querySelector("[data-testid='live-task-']")).toBeNull();
  });

  it("M1 创建对话框：开合+五要素门槛清单（gate-0 现状/gate-4 目标态同源安全三件）+200U+裁决门+demo 标注；提交仅本地提示", async () => {
    mount();
    await screen.findByTestId("live-b1");
    fireEvent.click(screen.getByTestId("live-m1-trigger"));
    const dialog = await screen.findByRole("dialog");
    // 五要素（CARD-03：数量 4→5；①现状④⑤目标态分档标注）
    for (let i = 0; i < 5; i++) {
      expect(within(dialog).getByTestId(`live-m1-gate-${i}`)).toBeTruthy();
    }
    // 配对锚：gate-0=创建三层校验【现状】；gate-4=安全三件【目标态】
    // （文本展开自 SAFETY_ITEMS 同源常量，与 B5 用例互证同源）
    const gate0 = within(dialog).getByTestId("live-m1-gate-0");
    expect(gate0.textContent).toContain("创建三层校验");
    expect(gate0.textContent).toContain("【现状】");
    const gate4 = within(dialog).getByTestId("live-m1-gate-4");
    expect(gate4.textContent).toContain("安全三件");
    expect(gate4.textContent).toContain("总回撤≥20% 全停");
    expect(gate4.textContent).toContain("【目标态】");
    // 引导句（CARD-03：四重→五要素，三层校验在役表述修正）
    expect(dialog.textContent).toContain("五要素门槛清单");
    expect(dialog.textContent).toContain("200U");
    expect(dialog.textContent).toContain("人工裁决门");
    expect(within(dialog).getByTestId("live-m1-submit").textContent).toContain("demo");
    // 合：提交后对话框关闭，创建动作不发送请求（demo 域如实披露）。
    // jsdom 下 transitionend 不触发，rc-motion 的关闭动画永不完成、dialog
    // 不卸载——以 leave 动画类（同步挂载）作为 open=false 的可判别证据。
    fireEvent.click(within(dialog).getByTestId("live-m1-submit"));
    expect(toast.info).toHaveBeenCalledWith("demo 域预览——创建动作不发送请求（实盘链未建设）");
    await waitFor(() =>
      expect(screen.getByRole("dialog").className).toContain("ant-zoom-leave"),
    );
  });
});
