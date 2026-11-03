import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock react-router
const mockNavigate = vi.fn();
let mockLocationState: Record<string, unknown> | null = null;

vi.mock("react-router", () => ({
  useLocation: () => ({ state: mockLocationState }),
  useNavigate: () => mockNavigate,
}));

// Mock API services
vi.mock("@/services/strategies", () => ({
  runBacktest: vi.fn(),
}));

vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(() => Promise.resolve({ data: [] })),
  getChartIndicators: vi.fn(() => Promise.resolve({
    ema: {}, boll: null, rsi: null, macd: null, kdj: null,
  })),
}));

vi.mock("@/stores/chart-settings", () => ({
  useChartSettings: vi.fn(() => ({
    emaList: [
      { period: 10, color: "#3B82F6", enabled: true },
    ],
    boll: { enabled: true, period: 20, std: 2.0, color: "#F59E0B" },
    rsi: { enabled: true, period: 14, overbought: 70, oversold: 30 },
    vol: { enabled: true, position: "overlay" },
    addEma: vi.fn(),
    removeEma: vi.fn(),
    updateEma: vi.fn(),
    reorderEma: vi.fn(),
    setBoll: vi.fn(),
    setRsi: vi.fn(),
    setVol: vi.fn(),
    resetToDefaults: vi.fn(),
    getEmaPeriods: vi.fn(() => [10]),
    getBollParams: vi.fn(() => ({ period: 20, std: 2 })),
    getIndicatorParams: vi.fn(),
  })),
}));

vi.mock("@/hooks/useStrategies", () => ({
  useCreateStrategy: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useStrategies: vi.fn(() => ({
    data: { items: [], total: 0 },
  })),
}));

vi.mock("@/hooks/useValidation", () => ({
  useValidateRules: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("@/hooks/useDatasets", () => ({
  useAvailableSources: vi.fn(() => ({
    queryKey: ["test"],
    queryFn: vi.fn(),
    staleTime: 5000,
  })),
}));

vi.mock("@/hooks/useChartIndicators", () => ({
  useChartIndicators: vi.fn(() => ({
    candleData: undefined,
    chartIndicators: [],
    chartBollData: undefined,
    volumeData: [],
    macdData: null,
    kdjData: null,
    isLoadingOhlcv: false,
    isLoadingIndicators: false,
  })),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// KlineChart mock：forwardRef+handle spy+onChartReady 触发形制
//（CARD-01 (b)/(e)——sl_series 经 setObservationSeries 命令式挂线；
// 重挂后补挂通道=onChartReady，mock 须模拟真组件的 ready 回调——
// 不调则互斥重挂场景线丢失，维度锚观测不到 handle 通道）
const chartMocks = vi.hoisted(() => ({ setObservationSeries: vi.fn() }));
vi.mock("@/components/charts/KlineChart", async () => {
  const { forwardRef, useImperativeHandle, useEffect } = await import("react");
  return {
    KlineChart: forwardRef(
      (props: { onChartReady?: (chart: never, series: never) => void },
              ref: React.Ref<unknown>) => {
        useImperativeHandle(ref, () => ({
          zoomIn: () => {},
          zoomOut: () => {},
          resetView: () => {},
          setObservationSeries: chartMocks.setObservationSeries,
        }));
        // 模拟真组件：图表创建即回调 onChartReady（互斥重挂的衔接锚）
        useEffect(() => {
          props.onChartReady?.({} as never, {} as never);
        }, []);
        return <div data-testid="kline-chart" />;
      }),
  };
});

// Mock EquityCurve（echarts canvas——jsdom 崩，沿 BacktestModePanel.test 形制）
vi.mock("@/components/charts/EquityCurve", () => ({
  EquityCurve: vi.fn(() => <div data-testid="equity-curve" />),
}));

// Mock BacktestMetricsPanel / StrategyDetail（回测完成态子面板）
vi.mock("@/components/lab/BacktestMetricsPanel", () => ({
  BacktestMetricsPanel: vi.fn(() => <div data-testid="backtest-metrics" />),
}));
vi.mock("@/components/StrategyDetail", () => ({
  StrategyDetail: vi.fn(() => <div data-testid="strategy-detail" />),
}));

vi.mock("motion/react", () => ({
  useReducedMotion: () => false,
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

import { Lab } from "@/pages/Lab";
import { runBacktest } from "@/services/strategies";
import { useValidateRules } from "@/hooks/useValidation";
import { mockDNA, mockBacktestResult } from "@/test/fixtures";

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        {children}
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  mockLocationState = null;
  vi.clearAllMocks();
});

describe("Lab - autoRun removal", () => {
  it("does NOT auto-run backtest when navigated from Evolution with DNA", () => {
    // Simulate navigation from Evolution page
    mockLocationState = {
      dna: mockDNA,
      symbol: "BTCUSDT",
      timeframe: "4h",
      dataStart: "2025-01-01",
      dataEnd: "2025-03-01",
    };

    render(<Lab />, { wrapper: createWrapper() });

    // runBacktest should NOT be called on mount
    expect(runBacktest).not.toHaveBeenCalled();
  });

  it("shows backtest mode when arriving with DNA route state", () => {
    mockLocationState = {
      dna: mockDNA,
      symbol: "BTCUSDT",
      timeframe: "4h",
      dataStart: "2025-01-01",
      dataEnd: "2025-03-01",
    };

    render(<Lab />, { wrapper: createWrapper() });

    // Should be in backtest mode (tab active)
    expect(screen.getByText("策略回测")).toBeInTheDocument();
  });

  it("defaults to hypothesis mode when no route state", () => {
    mockLocationState = null;

    render(<Lab />, { wrapper: createWrapper() });

    // Should show hypothesis mode UI elements
    expect(screen.getByText("假设验证")).toBeInTheDocument();
    expect(screen.getByText("规律构建器")).toBeInTheDocument();
  });

  it("shows collapsed config summary with symbol when in backtest mode with DNA", () => {
    mockLocationState = {
      dna: mockDNA,
      symbol: "BTCUSDT",
      timeframe: "4h",
    };

    render(<Lab />, { wrapper: createWrapper() });

    // Config panel should be collapsed (showing summary) with the symbol
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    // runBacktest should NOT have been called (user needs to click)
    expect(runBacktest).not.toHaveBeenCalled();
  });

  it("has three mode tabs: hypothesis, backtest, scene", () => {
    render(<Lab />, { wrapper: createWrapper() });

    expect(screen.getByText("假设验证")).toBeInTheDocument();
    expect(screen.getByText("策略回测")).toBeInTheDocument();
    expect(screen.getByText("场景验证")).toBeInTheDocument();
  });
});

describe("Lab - sl_series observation series (CARD-01)", () => {
  // series 档止损线样本（bridge/REST [{time,value}] 形制——value 可 null
  // =SL 未定义期；维度锚只验 Lab 传递，NaN→null 翻译锚在 REST 面测试）
  const slSeriesData = [
    { time: "2025-01-01 00:00:00+00:00", value: 95000 },
    { time: "2025-01-02 00:00:00+00:00", value: null },
    { time: "2025-01-03 00:00:00+00:00", value: 94500 },
  ];

  /** 完整真实用户路径：hypothesis 跑规则验证挂 KlineChart（KlineChart
   * 仅在规则验证结果块内渲染）→ 切 backtest 跑策略回测（onResult →
   * setSlSeries）→ 切回 hypothesis（KlineChart 重挂，onChartReady 衔
   * 接锚+effect 双通道）→ 观测 handle 通道收到的序列。 */
  async function fullFlow(stateSymbol: string, stateTimeframe: string) {
    mockLocationState = {
      dna: mockDNA,
      symbol: stateSymbol,
      timeframe: stateTimeframe,
      dataStart: "2025-01-01",
      dataEnd: "2025-03-01",
    };
    // 规则验证 mutateAsync resolve 最小结果（撑结果块渲染）
    vi.mocked(useValidateRules).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        total_trades: 0, buy_signals: [], sell_signals: [],
        win_rate: null, win_trades: 0, loss_trades: 0,
        total_return_pct: 0, avg_return_pct: 0,
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useValidateRules>);
    vi.mocked(runBacktest).mockResolvedValue({
      ...mockBacktestResult,
      sl_series: slSeriesData,
    });
    render(<Lab />, { wrapper: createWrapper() });

    // 1) 切 hypothesis tab，添加入场+出场条件（各默认一行）
    fireEvent.click(screen.getByText("假设验证"));
    const addBtns = screen.getAllByText("添加条件");
    fireEvent.click(addBtns[0]); // 入场条件组
    fireEvent.click(addBtns[1]); // 出场条件组
    // 2) 规则验证 → KlineChart 挂载
    fireEvent.click(screen.getByText("验证策略"));
    await waitFor(() => {
      expect(screen.getByTestId("kline-chart")).toBeInTheDocument();
    });

    // 3) 切 backtest 跑策略回测（routeState.dna → 折叠态先展开）
    fireEvent.click(screen.getByText("策略回测"));
    fireEvent.click(screen.getByText("配置"));
    fireEvent.click(screen.getByText("运行回测"));
    await waitFor(() => {
      expect(runBacktest).toHaveBeenCalledTimes(1);
    });

    // 4) 切回 hypothesis（KlineChart 重挂）
    fireEvent.click(screen.getByText("假设验证"));
    await waitFor(() => {
      expect(screen.getByTestId("kline-chart")).toBeInTheDocument();
    });
  }

  it("attaches observation series when backtest dimensions match main chart", async () => {
    // 主图 pair=BTCUSDT/timeframe=15m（Lab 固定初始值）——回测维度全匹配
    await fullFlow("BTCUSDT", "15m");

    expect(chartMocks.setObservationSeries).toHaveBeenLastCalledWith(slSeriesData);
  });

  it("never attaches data when backtest symbol mismatches main chart pair", async () => {
    // 回测 ETHUSDT、主图 BTCUSDT——维度不匹配自失效（Q3 根治语义：
    // 换「消费即清」为「维度不匹配不画」），handle 通道永收不到数据
    await fullFlow("ETHUSDT", "15m");

    const received = chartMocks.setObservationSeries.mock.calls.map(
      (c: unknown[]) => c[0]);
    expect(received.length).toBeGreaterThan(0); // KlineChart 挂载期 effect 已跑
    expect(received.every((d) => d === null)).toBe(true);
  });

  it("never attaches data when backtest timeframe mismatches main chart", async () => {
    // 回测 4h、主图 15m——timeframe 维度同样参与匹配判定
    await fullFlow("BTCUSDT", "4h");

    const received = chartMocks.setObservationSeries.mock.calls.map(
      (c: unknown[]) => c[0]);
    expect(received.every((d) => d === null)).toBe(true);
  });
});

describe("Lab - 五步真实落点与 K 线常驻（fe-research-execution-domain 1.2）", () => {
  /** 步骤条当前步判定：nav 内指定步名按钮持有当前步高亮 class */
  function currentStepName(): string {
    const nav = screen.getByLabelText("假设流水线步骤");
    const active = Array.from(nav.querySelectorAll("button")).find(
      (b) => b.className.includes("bg-accent-gold/10"),
    );
    return active?.textContent?.replace(/^\d+/, "") ?? "";
  }

  it("hypothesis 无结果：步①构建为当前步，中列 K 线已常驻在页", () => {
    render(<Lab />, { wrapper: createWrapper() });
    expect(currentStepName()).toBe("构建");
    // K 线常驻：未验证时中列即在（含引导注）
    expect(screen.getByTestId("kline-chart")).toBeInTheDocument();
    expect(screen.getByText("构建并验证后叠加买卖信号")).toBeInTheDocument();
  });

  it("backtest 模式（routeState.dna 进入）：步④沙盒为当前步", () => {
    mockLocationState = { dna: mockDNA };
    render(<Lab />, { wrapper: createWrapper() });
    expect(currentStepName()).toBe("沙盒");
  });

  it("hypothesis 验证完成：步③案例为当前步（结果即案例面）", async () => {
    vi.mocked(useValidateRules).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        total_trades: 0, buy_signals: [], sell_signals: [],
        win_rate: null, win_trades: 0, loss_trades: 0,
        total_return_pct: 0, avg_return_pct: 0,
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useValidateRules>);
    render(<Lab />, { wrapper: createWrapper() });
    const addBtns = screen.getAllByText("添加条件");
    fireEvent.click(addBtns[0]);
    fireEvent.click(addBtns[1]);
    fireEvent.click(screen.getByText("验证策略"));
    await waitFor(() => {
      expect(currentStepName()).toBe("案例");
    });
  });
});

describe("Lab - 保存链顺序用例（t3r4 CARD-A：验证→改参→保存→断言新值）", () => {
  it("验证后改止损参数再保存：risk_genes 取面板当前值而非验证时刻旧值", async () => {
    const saveMutate = vi.fn().mockResolvedValue({});
    vi.mocked(useValidateRules).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        total_trades: 3,
        buy_signals: [{ time: "2025-01-01 00:00:00+00:00" }],
        sell_signals: [],
        win_rate: 60, win_trades: 2, loss_trades: 1,
        total_return_pct: 5, avg_return_pct: 1.6,
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useValidateRules>);
    const { useCreateStrategy } = await import("@/hooks/useStrategies");
    vi.mocked(useCreateStrategy).mockReturnValue({
      mutateAsync: saveMutate,
      isPending: false,
    } as never);

    render(<Lab />, { wrapper: createWrapper() });
    // 1) hypothesis 验证（ruleResult 落地）
    const addBtns = screen.getAllByText("添加条件");
    fireEvent.click(addBtns[0]);
    fireEvent.click(addBtns[1]);
    fireEvent.click(screen.getByText("验证策略"));
    await waitFor(() => {
      expect(screen.getByText(/沙盒回测 · 不计判定并集/)).toBeInTheDocument();
    });

    // 2) 切 backtest 展开高级参数改止损 0.05→0.08
    fireEvent.click(screen.getByText("策略回测"));
    fireEvent.click(screen.getByText("高级参数"));
    const stopInput = screen.getByDisplayValue("0.05");
    fireEvent.change(stopInput, { target: { value: "0.08" } });

    // 3) 切回 hypothesis 保存为策略
    fireEvent.click(screen.getByText("假设验证"));
    // 构器操作行与结果面板双钮并存——取结果面板位（第二个）
    fireEvent.click(screen.getAllByText("保存为策略")[1]);
    fireEvent.change(screen.getByPlaceholderText("输入策略名称"), {
      target: { value: "顺序用例策略" },
    });
    fireEvent.click(screen.getByText("确认保存"));

    // 4) 断言：保存的 risk_genes.stop_loss 是改后的 0.08（旧闭包则存 0.05）
    await waitFor(() => {
      expect(saveMutate).toHaveBeenCalledTimes(1);
    });
    const saved = saveMutate.mock.calls[0][0] as {
      dna: { risk_genes: { stop_loss: number } };
    };
    expect(saved.dna.risk_genes.stop_loss).toBe(0.08);

    // G2（t3r4）：保存成功=步⑤登记完成态——currentStep=4（「登记」当前+
    // 完成文案）；同流程顺带覆盖，无需独立驱动
    await waitFor(() => {
      expect(screen.getByText(/已登记（步⑤完成）/)).toBeInTheDocument();
    });
    const nav = screen.getByLabelText("假设流水线步骤");
    const active = Array.from(nav.querySelectorAll("button")).find(
      (b) => b.className.includes("bg-accent-gold/10"),
    );
    expect(active?.textContent).toBe("5登记");
  });
});
