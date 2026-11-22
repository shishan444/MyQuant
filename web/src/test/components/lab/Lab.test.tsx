/**
 * Lab 页单模式形制锚（fix-replay-gap-alignment U4/U5 改写版）：
 * 回测/场景两模式与验证→保存动作链随死端点退役后，页面保留 hypothesis
 * 单模式——条件构建器+K 线常驻+步①构建锚。旧用例族（autoRun/三模式
 * tab/sl_series 挂线/保存链顺序）随死语义整族退役。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

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

// KlineChart mock：forwardRef+handle 形制（ChartToolbar 经 ref 调视口命令）
vi.mock("@/components/charts/KlineChart", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    KlineChart: forwardRef((_props: unknown, ref: React.Ref<unknown>) => {
      useImperativeHandle(ref, () => ({
        zoomIn: () => {},
        zoomOut: () => {},
        resetView: () => {},
        setObservationSeries: vi.fn(),
      }));
      return <div data-testid="kline-chart" />;
    }),
  };
});

vi.mock("motion/react", () => ({
  useReducedMotion: () => false,
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
  AnimatePresence: ({ children }: any) => <>{children}</>,
}));

import { Lab } from "@/pages/Lab";

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
  vi.clearAllMocks();
});

describe("Lab - hypothesis 单模式（U4/U5 退役改写）", () => {
  it("规律构建器在页，模式切换器零残留（回测/场景随死端点退役）", () => {
    render(<Lab />, { wrapper: createWrapper() });

    expect(screen.getByText("规律构建器")).toBeInTheDocument();
    // 负断言：三模式切换器退役
    expect(screen.queryByText("假设验证")).toBeNull();
    expect(screen.queryByText("策略回测")).toBeNull();
    expect(screen.queryByText("场景验证")).toBeNull();
    // 负断言：验证/保存动作链退役
    expect(screen.queryByText("验证策略")).toBeNull();
    expect(screen.queryByText("保存为策略")).toBeNull();
  });

  it("K 线常驻在页（数据源联动主图——不依赖验证动作）", () => {
    render(<Lab />, { wrapper: createWrapper() });
    expect(screen.getByTestId("kline-chart")).toBeInTheDocument();
  });

  it("步①构建为当前步（进度锚定构建步——验证链退役后恒定）", () => {
    render(<Lab />, { wrapper: createWrapper() });
    const nav = screen.getByLabelText("假设流水线步骤");
    const active = Array.from(nav.querySelectorAll("button")).find(
      (b) => b.className.includes("bg-accent-gold/10"),
    );
    expect(active?.textContent).toBe("1构建");
  });

  it("条件增删交互：添加条件各一行，清空全部归零", () => {
    render(<Lab />, { wrapper: createWrapper() });

    const addBtns = screen.getAllByText("添加条件");
    expect(addBtns).toHaveLength(2);
    fireEvent.click(addBtns[0]); // 入场条件组
    fireEvent.click(addBtns[1]); // 出场条件组
    // 各组条件行在页（行内删除钮 aria-label="delete condition"）
    expect(screen.getAllByLabelText("delete condition")).toHaveLength(2);

    fireEvent.click(screen.getByText("清空全部"));
    expect(screen.queryByLabelText("delete condition")).toBeNull();
  });
});
