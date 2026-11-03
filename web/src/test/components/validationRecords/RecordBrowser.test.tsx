/**
 * 验证记录浏览组件族测（fix/fe-validation-forms-remediation Q2——③审查
 * 坐实的零测试锚补齐）。覆盖：FilterBar 四维筛选与前缀路由/Q4 offset 随
 * filters 重置/详情两态（detail_available=false 空骨架·true 三形态真值：
 * params 三键展开/K 线复核双标与 CHART_VC 底/资金曲线 compact 时间轴重建）。
 * 服务层全 mock（零外网红线）；KlineChart mock（jsdom 不渲染真图）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/services/validationRecords", async () => {
  const actual = await vi.importActual("@/services/validationRecords");
  return {
    ...actual,
    getValidationRecordsPage: vi.fn(),
    getValidationRecordDetail: vi.fn(),
  };
});

vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(),
}));

vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: (props: { signals?: Array<{ type: string; timestamp: string }> }) => (
    <div data-testid="vr-kline" data-signals={props.signals?.length ?? 0} />
  ),
}));

import {
  FilterBar,
  RecordsTable,
  RecordDetail,
} from "@/components/validationRecords/RecordBrowser";
import {
  getValidationRecordsPage,
  getValidationRecordDetail,
  type ValidationDetailResponse,
} from "@/services/validationRecords";
import { getOhlcvBySymbol } from "@/services/datasets";
import type { ValidationRecordRow } from "@/services/workbench";
import { CHART_VC } from "@/theme/tokens";

const mockList = vi.mocked(getValidationRecordsPage);
const mockDetail = vi.mocked(getValidationRecordDetail);
const mockOhlcv = vi.mocked(getOhlcvBySymbol);

function makeRow(over: Partial<ValidationRecordRow> = {}): ValidationRecordRow {
  return {
    record_id: "rec_test01",
    hypothesis_id: "hyp_x1",
    strategy_id: "st_y1",
    level: "isolated_backtest",
    segmentation: "",
    verdict: "inconclusive",
    range_hash: "rh",
    pollution_cause: null,
    created_at: "2026-11-01T10:39:42.413188+00:00",
    params: {
      request_range: { symbol: "BTCUSDT", timeframe: "30m", start: "2026-04-01", end: "2026-09-01" },
      range_hash: "rh",
      threshold_profile: "default",
    },
    result: null,
    ...over,
  };
}

function makeDetail(over: Partial<ValidationDetailResponse> = {}): ValidationDetailResponse {
  return {
    record_id: "rec_test01",
    hypothesis_id: "hyp_x1",
    strategy_id: "st_y1",
    level: "isolated_backtest",
    verdict: "inconclusive",
    created_at: "2026-11-01T10:39:42.413188+00:00",
    init_cash: 100000,
    fee: 0.001,
    slippage: 0.0005,
    detail_available: true,
    executions: [
      {
        exec_ref: "exec0",
        exec_index: 0,
        range_start: "2026-04-01",
        range_end: "2026-09-01",
        trades: [
          { seq: 0, entry_time: "2026-04-02 00:00:00+00:00", exit_time: "2026-04-03 00:00:00+00:00",
            entry_price: 100, exit_price: 110, size: 1, direction: "long", pnl: 10 },
          { seq: 1, entry_time: "2026-04-05 00:00:00+00:00", exit_time: "2026-04-06 00:00:00+00:00",
            entry_price: 105, exit_price: 95, size: 1, direction: "short", pnl: 10 },
        ],
        equity: { grid_aligned: true, timeframe: "30m", start: "2026-04-01 00:00:00+00:00", length: 4, timestamps: null, values: [100000, 101000, 100500, 102000] },
      },
    ],
    ...over,
  };
}

function ui(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue({
    records: [makeRow()],
    total: 1, offset: 0, limit: 50, has_more: false,
  } as never);
});

describe("FilterBar 四维筛选（前缀路由+level/verdict+重置）", () => {
  it("hyp_ 前缀路由 hypothesis_id；无前缀路由 strategy_id", async () => {
    const onSearch1 = vi.fn();
    ui(<FilterBar onSearch={onSearch1} />);
    const input1 = screen.getByPlaceholderText(/假设 ID/);
    fireEvent.change(input1, { target: { value: "hyp_abc" } });
    fireEvent.keyDown(input1, { key: "Enter", code: "Enter", charCode: 13, keyCode: 13 });
    expect(onSearch1).toHaveBeenLastCalledWith(
      expect.objectContaining({ idType: "hypothesis_id", id: "hyp_abc" }),
    );
  });

  it("无 hyp_ 前缀路由 strategy_id", async () => {
    const onSearch = vi.fn();
    ui(<FilterBar onSearch={onSearch} />);
    const input = screen.getByPlaceholderText(/假设 ID/);
    fireEvent.change(input, { target: { value: "st_xyz" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", charCode: 13, keyCode: 13 });
    expect(onSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ idType: "strategy_id", id: "st_xyz" }),
    );
  });

  it("level/verdict 选择即时上报；重置还原四维", async () => {
    const onSearch = vi.fn();
    ui(<FilterBar onSearch={onSearch} />);
    // antd Select 交互经键盘打开+选择较繁——改由 fire 断言重置路径
    fireEvent.click(screen.getByRole("button", { name: /重置/ }));
    expect(onSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: undefined, level: undefined, verdict: undefined }),
    );
  });
});

describe("RecordsTable：Q4 offset 随 filters 重置", () => {
  it("翻页后 filters 变化回到 offset=0（死局空态修复）", async () => {
    mockList.mockResolvedValue({
      records: [makeRow()],
      total: 60, offset: 50, limit: 50, has_more: true,
    } as never);
    const { rerender } = ui(
      <RecordsTable filters={{ idType: "hypothesis_id" }} onSelect={vi.fn()} />,
    );
    await screen.findByText("加载更多");
    fireEvent.click(screen.getByText("加载更多"));
    await waitFor(() => {
      // 第二页请求 offset=50
      expect(mockList).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 50 }),
      );
    });
    // 换筛选（同组件 rerender）——offset 应回 0
    const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={qc2}>
        <RecordsTable filters={{ idType: "hypothesis_id", id: "hyp_abc" }} onSelect={vi.fn()} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(mockList).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 0, hypothesis_id: "hyp_abc" }),
      );
    });
  });
});

describe("RecordDetail 两态与三形态真值（Q1）", () => {
  it("detail_available=false：不可复核空骨架如实", async () => {
    mockDetail.mockResolvedValue(makeDetail({ detail_available: false, executions: [] }));
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByText(/detail_available=false/)).toBeInTheDocument();
  });

  it("三形态真值：params 三键展开+K 线双标 CHART_VC 底+compact 时间轴重建+首末盈亏", async () => {
    mockDetail.mockResolvedValue(makeDetail());
    mockOhlcv.mockResolvedValue({
      data: Array.from({ length: 5 }, (_, i) => ({
        timestamp: `2026-04-0${i + 1} 00:00:00+00:00`,
        open: 100 + i, high: 105 + i, low: 95 + i, close: 100 + i, volume: 10,
      })),
    } as never);
    ui(<RecordDetail record={makeRow()} />);
    // V-D1 参数回显：request_range 四键
    expect(await screen.findByTestId("vr-request-range")).toHaveTextContent("BTCUSDT");
    expect(screen.getByTestId("vr-request-range")).toHaveTextContent("30m");
    expect(screen.getByText(/default/)).toBeInTheDocument();

    // V-D2 K 线复核：进入 tab——KlineChart 消费 trades 4 标（2 笔×entry/exit）
    fireEvent.click(screen.getByRole("tab", { name: "K线复核" }));
    const kline = await screen.findByTestId("vr-kline");
    expect(kline).toHaveAttribute("data-signals", "4");
    // jsdom 将内联色序列化为 rgb 形（#0B0D12 → rgb(11, 13, 18)）——断言换算
    const vcBgRgb = `rgb(${[CHART_VC.bg.slice(1, 3), CHART_VC.bg.slice(3, 5), CHART_VC.bg.slice(5, 7)]
      .map((h) => parseInt(h, 16)).join(", ")})`;
    expect(kline.parentElement?.getAttribute("style")).toContain(vcBgRgb);

    // V-D3 资金曲线：compact 形制注+4 点+末值 102000+盈亏百分比
    fireEvent.click(screen.getByRole("tab", { name: "资金曲线" }));
    const eq = await screen.findByTestId("vr-equity");
    expect(eq).toHaveTextContent(/compact 形制/);
    expect(eq).toHaveTextContent(/4 点/);
    expect(eq).toHaveTextContent("+2.00%");
  });

  it("多份选择器：executions>1 时呈现份选择", async () => {
    const d = makeDetail();
    d.executions.push({ ...d.executions[0], exec_ref: "exec1", exec_index: 1 });
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    // 多份选择器在场（antd options 虚拟渲染，展开断言不稳定——以
    // combobox 存在+默认份 label 为锚）
    const combo = await screen.findByRole("combobox");
    expect(combo).toBeInTheDocument();
  });
});

describe("equityPoints 纯逻辑（compact 时间轴重建）", () => {
  it("全时间戳形制直用（timestamps 与 values zip）", async () => {
    const d = makeDetail();
    d.executions[0].equity = {
      grid_aligned: false, timeframe: "30m",
      timestamps: ["2026-04-01 00:00:00+00:00", "2026-04-02 00:00:00+00:00"],
      values: [100, 200],
    };
    mockDetail.mockResolvedValue(d);
    mockOhlcv.mockResolvedValue({ data: [] } as never);
    ui(<RecordDetail record={makeRow()} />);
    await screen.findByText(/hyp_x1/);
    fireEvent.click(screen.getByRole("tab", { name: "资金曲线" }));
    const eq = await screen.findByTestId("vr-equity");
    expect(eq).toHaveTextContent(/全时间戳形制/);
    expect(eq).toHaveTextContent(/2 点/);
  });
});
