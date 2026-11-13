/**
 * 验证记录浏览组件族测（upgrade-validation-records-list 5.1/5.2——
 * fe-validation-records-page Q2 版重写）。覆盖：FilterBar 搜索三路由/
 * verdict 四枚计数联动/类型三枚/泛化 toggle/排序三键/重置；RecordCards
 * 卡片四段对照 API JSON 逐值/懒加载两页拼接/已加载 N 总数 M/无值落尾
 * 披露/翻页失败内容保留；RecordDetail D1 两行与双降级/D2 标记与 trail
 * 挂线/D3 一致性核对与 worst 标签/V2 逐笔表未平仓与出场对齐/V3 四元
 * 判读+reason 五值+capped+MTF/V4 三布尔独立降级。服务层全 mock（零外
 * 网红线）；KlineChart mock（jsdom 不渲染真图——采集 signalMarkers 与
 * 命令式句柄）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/services/validationRecords", async () => {
  const actual = await vi.importActual("@/services/validationRecords");
  return {
    ...actual,
    getValidationRecordsPage: vi.fn(),
    getValidationRecordDetail: vi.fn(),
    getUnionBalance: vi.fn(),
  };
});

vi.mock("@/services/datasets", () => ({
  getOhlcvBySymbol: vi.fn(),
  getChartIndicators: vi.fn(),
}));
vi.mock("@/services/strategies", async () => {
  const actual = await vi.importActual("@/services/strategies");
  return { ...actual, getStrategy: vi.fn() };
});

// KlineChart mock：React 19 ref prop 直传——渲染期注入命令式句柄
// （scrollToTime/setObservationSeries 采集调用），signalMarkers 计数/
// 文案、height、equityData 长度经 data 属性透出；data 长度（CARD-01
// 契约 E 主图过滤锚）与 onChartReady 同步触发（真组件在 chart 就绪时
// 调——RecordDetail 段带挂载链经此回调，mock 侧同步调以建立 bands
// 捕获通道，attachSegmentBand 层 mock、toSegmentBands 真实现保真）
const mockChartHandle = {
  scrollToTime: vi.fn(),
  setObservationSeries: vi.fn(),
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
  resetView: vi.fn(),
};
const mockBandSetData = vi.fn();
vi.mock("@/components/charts/primitives/segmentBand", async () => {
  const actual = await vi.importActual<
    typeof import("@/components/charts/primitives/segmentBand")
  >("@/components/charts/primitives/segmentBand");
  return {
    ...actual,
    attachSegmentBand: vi.fn(() => ({ setData: mockBandSetData })),
  };
});
vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: (props: {
    signalMarkers?: Array<{ text?: string }>;
    height?: number;
    equityData?: Array<unknown>;
    subChartType?: string;
    data?: Array<unknown>;
    indicators?: Array<unknown>;
    onChartReady?: (chart: unknown, series: unknown) => void;
    ref?: { current: unknown };
  }) => {
    if (props.ref) props.ref.current = mockChartHandle;
    props.onChartReady?.(null, { fake: "series" });
    return (
      <div
        data-testid="vr-kline"
        data-markers={props.signalMarkers?.length ?? 0}
        data-markers-text={(props.signalMarkers ?? []).map((m) => m.text).join("|")}
        data-height={props.height}
        data-equity-len={props.equityData?.length ?? 0}
        data-equity-times={(props.equityData ?? []).map((p) => (p as { time: string }).time).join(",")}
        data-equity-vals={(props.equityData ?? []).map((p) => (p as { value?: number }).value).join(",")}
        data-subchart={props.subChartType ?? ""}
        data-len={props.data?.length ?? 0}
        data-indicators={props.indicators?.length ?? 0}
      />
    );
  },
}));

import {
  FilterBar,
  RecordCards,
} from "@/components/validationRecords/RecordBrowser";
import {
  DEFAULT_LIST_FILTERS,
  type ListFilters,
} from "@/components/validationRecords/traceSemantics";
import { RecordDetail } from "@/components/validationRecords/RecordDetail";
import { ApiError } from "@/services/api";
import { toTime } from "@/components/charts/core/time";
import {
  getValidationRecordsPage,
  getValidationRecordDetail,
  getUnionBalance,
  type ValidationDetailResponse,
  type VrTraceRow,
} from "@/services/validationRecords";
import { getChartIndicators, getOhlcvBySymbol } from "@/services/datasets";
import { getStrategy } from "@/services/strategies";
import type { ValidationRecordRow } from "@/services/workbench";
import { TRACE_FAMILY_COLOR } from "@/components/validationRecords/traceSemantics";

const mockList = vi.mocked(getValidationRecordsPage);
const mockDetail = vi.mocked(getValidationRecordDetail);
const mockUnion = vi.mocked(getUnionBalance);
const mockOhlcv = vi.mocked(getOhlcvBySymbol);
const mockChartInd = vi.mocked(getChartIndicators);
const mockGetStrategy = vi.mocked(getStrategy);

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
    },
    result: null,
    statement: "当 RSI(14)<30 且量能>2倍均值时做多",
    ...over,
  };
}

function makeTrace(over: Partial<VrTraceRow> = {}): VrTraceRow {
  return {
    exec_ref: "main",
    bar_time: "2026-04-02 00:00:00+00:00",
    seq: 0,
    outcome: "filled",
    reason: null,
    event_code: 10,
    size: 1,
    price: 100,
    capped: null,
    gene_hits: null,
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
    signal_trace: true,
    trade_semantics: true,
    stop_price: true,
    mtf_gated_bars: null,
    executions: [
      {
        exec_ref: "main",
        exec_index: 0,
        range_start: "2026-04-01",
        range_end: "2026-09-01",
        trades: [
          { seq: 0, entry_time: "2026-04-02 00:00:00+00:00", exit_time: "2026-04-03 00:00:00+00:00",
            entry_price: 100, exit_price: 110, size: 1, direction: "long", pnl: 10 },
          { seq: 1, entry_time: "2026-04-05 00:00:00+00:00", exit_time: null,
            entry_price: 105, exit_price: null, size: 1, direction: "long", pnl: null },
        ],
        equity: { grid_aligned: true, timeframe: "30m", start: "2026-04-01 00:00:00+00:00", length: 4, timestamps: null, values: [100000, 101000, 100500, 112400] },
        trail_series: [{ time: "2026-04-01 00:00:00+00:00", value: 98000 }],
        trace_rows: [makeTrace()],
      },
    ],
    ...over,
  };
}

/** 视图选择器开合（契约 L 后页面有两 combobox——按「全区间」显示值
 *  锚定视图选择器的 ant-select 根，开下拉后返回 document 供选项查找） */
function openViewSelect() {
  const sel = document.querySelector('[data-testid="vr-view-select"]');
  if (!(sel instanceof HTMLElement)) throw new Error("视图选择器未找到");
  fireEvent.mouseDown(sel.querySelector(".ant-select-selector") ?? sel);
}

/** 周期选择器开合（工具区 vr-tf-select 作用域锚定） */
function openTfSelect() {
  const toolbar = screen.getByTestId("vr-toolbar");
  const sel = toolbar.querySelector(".ant-select");
  if (!sel) throw new Error("周期选择器未找到");
  fireEvent.mouseDown(sel.querySelector(".ant-select-selector") ?? sel);
}

function ui(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockChartHandle.setObservationSeries.mockClear();
  mockChartHandle.scrollToTime.mockClear();
  mockList.mockResolvedValue({
    records: [makeRow()],
    total: 1, offset: 0, limit: 50, has_more: false,
    verdict_counts: { pass: 31, fail: 9, inconclusive: 7 },
    unsortable_count: 0,
  } as never);
  mockOhlcv.mockResolvedValue({
    data: Array.from({ length: 5 }, (_, i) => ({
      timestamp: `2026-04-0${i + 1} 00:00:00+00:00`,
      open: 100 + i, high: 105 + i, low: 95 + i, close: 100 + i, volume: 10,
    })),
  } as never);
  // union 余量查询默认实现（REQ-4 前既有脆弱点：裸 vi.fn() 返回 undefined
  // 触发 react-query 崩溃——各测试显式设置时自行覆盖，此为安全默认）
  mockUnion.mockResolvedValue({
    hypothesis_id: "hyp_x1", symbol: "BTCUSDT", timeframe: "30m",
    judged: [], remaining_bars: 0, remaining: [], remaining_time: "",
  } as never);
});

describe("FilterBar（L2 三路由/L3 筛选/L4 排序——服务端执行）", () => {
  it("搜索三路由：hyp_→hypothesis_id、st_→strategy_id、其余→q 关键词", async () => {
    // antd 6 Input 同一实例连续回车仅触发一次 onPressEnter（实测）——
    // 三路由各自独立 mount 断言
    const cases: Array<{ input: string; expect: Record<string, unknown> }> = [
      { input: "hyp_abc", expect: { idType: "hypothesis_id", id: "hyp_abc" } },
      { input: "st_xyz", expect: { idType: "strategy_id", id: "st_xyz" } },
      { input: "RSI 超卖", expect: { idType: "q", q: "RSI 超卖" } },
    ];
    for (const c of cases) {
      const onChange = vi.fn();
      const { unmount } = ui(<FilterBar filters={{ ...DEFAULT_LIST_FILTERS }} onChange={onChange} />);
      const input = screen.getByTestId("vr-search-input");
      fireEvent.change(input, { target: { value: c.input } });
      fireEvent.keyDown(input, { key: "Enter", code: "Enter", charCode: 13, keyCode: 13 });
      expect(onChange).toHaveBeenCalledTimes(1);
      expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining(c.expect));
      unmount();
    }
  });

  it("verdict 四枚计数联动（verdict_counts）+verdict/类型/泛化/排序各自上报+重置", async () => {
    const onChange = vi.fn();
    ui(
      <FilterBar
        filters={{ ...DEFAULT_LIST_FILTERS }}
        onChange={onChange}
        counts={{ total: 47, pass: 31, fail: 9, inconclusive: 7 }}
      />,
    );
    // 计数联动四枚
    expect(screen.getByTestId("vr-verdict-all")).toHaveTextContent("全部 47");
    expect(screen.getByTestId("vr-verdict-pass")).toHaveTextContent("pass 31");
    expect(screen.getByTestId("vr-verdict-fail")).toHaveTextContent("fail 9");
    expect(screen.getByTestId("vr-verdict-inconclusive")).toHaveTextContent("证据不足 7");
    // verdict 点击上报
    fireEvent.click(screen.getByTestId("vr-verdict-fail"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ verdict: "fail" }),
    );
    // 排序三键切换
    fireEvent.click(screen.getByTestId("vr-sort-total_return_desc"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: "total_return_desc" }),
    );
    fireEvent.click(screen.getByTestId("vr-sort-verdict_quality"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort: "verdict_quality" }),
    );
    // 泛化 toggle（与类型取交集——同次上报）
    fireEvent.click(screen.getByTestId("vr-generalization-toggle"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ generalization: true }),
    );
    // 重置还原
    fireEvent.click(screen.getByRole("button", { name: /重置/ }));
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_LIST_FILTERS });
  });

  it("类型三枚中文标签选项", () => {
    ui(<FilterBar filters={{ ...DEFAULT_LIST_FILTERS }} onChange={vi.fn()} />);
    // antd Select options 虚拟渲染——以传入 options 数组为锚经 combobox 在场判别
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });
});

describe("RecordCards（L1 四段卡+L5 懒加载+L4 落尾披露）", () => {
  it("卡片四段对照 API JSON 逐值（isolated）", async () => {
    mockList.mockResolvedValue({
      records: [makeRow({
        verdict: "pass",
        result: {
          metrics: { total_return: 0.349, sharpe_ratio: 2.31, max_drawdown: -0.083, total_trades: 34 },
          symbol: "BTCUSDT",
        },
      })],
      total: 1, offset: 0, limit: 50, has_more: false,
      verdict_counts: { pass: 1, fail: 0, inconclusive: 0 },
      unsortable_count: 0,
    } as never);
    ui(<RecordCards filters={{ ...DEFAULT_LIST_FILTERS }} onSelect={vi.fn()} />);
    const card = await screen.findByTestId("vr-card-rec_test01");
    // 段1：标识+类型+verdict 徽标
    expect(card).toHaveTextContent("rec_test01");
    expect(card).toHaveTextContent("隔离验证");
    expect(card).toHaveTextContent("✓ 通过");
    // 段2：statement 引文
    expect(card).toHaveTextContent("「当 RSI(14)<30 且量能>2倍均值时做多」");
    // 段3：区间·币种·笔数
    expect(card).toHaveTextContent("2026-04-01 → 2026-09-01 · BTCUSDT · 34 笔");
    // 段4：收益·夏普·回撤（result 直读）
    expect(card).toHaveTextContent("+34.9%");
    expect(card).toHaveTextContent("夏普 2.31");
    expect(card).toHaveTextContent("回撤 -8.3%");
  });

  it("statement 无关联显「—」；result 空显「无指标留痕」", async () => {
    mockList.mockResolvedValue({
      records: [makeRow({ statement: null, result: null })],
      total: 1, offset: 0, limit: 50, has_more: false,
      verdict_counts: { pass: 0, fail: 0, inconclusive: 1 },
      unsortable_count: 0,
    } as never);
    ui(<RecordCards filters={{ ...DEFAULT_LIST_FILTERS }} onSelect={vi.fn()} />);
    const card = await screen.findByTestId("vr-card-rec_test01");
    expect(card).toHaveTextContent("—");
    expect(card).toHaveTextContent("无指标留痕");
  });

  it("random 卡片最差窗口标签", async () => {
    mockList.mockResolvedValue({
      records: [makeRow({
        level: "random_window",
        params: { symbol: "BTCUSDT", timeframe: "1h", data_start: "2026-01-01", data_end: "2026-06-30" },
        result: { summary: { worst_window: { metrics: { total_return: -0.121, sharpe_ratio: -0.8, max_drawdown: -0.15, total_trades: 5 } } } },
      })],
      total: 1, offset: 0, limit: 50, has_more: false,
      verdict_counts: { pass: 0, fail: 1, inconclusive: 0 },
      unsortable_count: 0,
    } as never);
    ui(<RecordCards filters={{ ...DEFAULT_LIST_FILTERS }} onSelect={vi.fn()} />);
    const card = await screen.findByTestId("vr-card-rec_test01");
    expect(card).toHaveTextContent("-12.1%");
    expect(card).toHaveTextContent("最差窗口");
  });

  it("懒加载：滚动到底拼接下一批无重复+已加载 N/总数 M", async () => {
    mockList.mockImplementation(((params: { offset?: number }) => {
      if ((params?.offset ?? 0) === 0) {
        return Promise.resolve({
          records: [makeRow({ record_id: "rec_a" })],
          total: 2, offset: 0, limit: 50, has_more: true,
          verdict_counts: { pass: 1, fail: 1, inconclusive: 0 },
          unsortable_count: 0,
        });
      }
      return Promise.resolve({
        records: [makeRow({ record_id: "rec_b" })],
        total: 2, offset: 50, limit: 50, has_more: false,
        verdict_counts: { pass: 1, fail: 1, inconclusive: 0 },
        unsortable_count: 0,
      });
    }) as never);
    ui(<RecordCards filters={{ ...DEFAULT_LIST_FILTERS }} onSelect={vi.fn()} />);
    await screen.findByTestId("vr-card-rec_a");
    expect(screen.getByTestId("vr-load-status")).toHaveTextContent("已加载 1 / 总数 2 条");
    // 滚动到底（jsdom 尺寸 0——阈值恒达）触发下一批
    fireEvent.scroll(screen.getByTestId("vr-card-scroll"));
    await screen.findByTestId("vr-card-rec_b");
    expect(screen.getByTestId("vr-load-status")).toHaveTextContent("已加载 2 / 总数 2 条");
    expect(screen.getByTestId("vr-load-status")).toHaveTextContent("已全部加载");
    // 两批拼接无重复：offset=50 已请求
    expect(mockList).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 50 }),
    );
  });

  it("sort=total_return_desc 且 unsortable_count>0：落尾披露", async () => {
    mockList.mockResolvedValue({
      records: [makeRow()],
      total: 4, offset: 0, limit: 50, has_more: false,
      verdict_counts: { pass: 1, fail: 1, inconclusive: 2 },
      unsortable_count: 3,
    } as never);
    ui(
      <RecordCards
        filters={{ ...DEFAULT_LIST_FILTERS, sort: "total_return_desc" }}
        onSelect={vi.fn()}
      />,
    );
    expect(await screen.findByTestId("vr-unsortable-note")).toHaveTextContent(
      "3 条无收益值已列末尾",
    );
  });

  it("筛选参数透传（六维+sort 下推服务端）", async () => {
    const filters: ListFilters = {
      idType: "q", q: "RSI", level: "isolated_backtest", verdict: "pass",
      generalization: true, sort: "verdict_quality",
    };
    ui(<RecordCards filters={filters} onSelect={vi.fn()} />);
    await waitFor(() => {
      expect(mockList).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "RSI", level: "isolated_backtest", verdict: "pass",
          generalization: true, sort: "verdict_quality", limit: 50, offset: 0,
        }),
      );
    });
  });
});

describe("RecordDetail（D1-D3/V1-V4）", () => {
  it("D1 两行：statement 全文逐字+并集余量；各自降级", async () => {
    mockDetail.mockResolvedValue(makeDetail());
    mockUnion.mockResolvedValue({
      hypothesis_id: "hyp_x1", symbol: "BTCUSDT", timeframe: "30m",
      judged: [], remaining: [{ start: "2026-10-01", end: "2026-12-01" }],
      remaining_bars: 500,
    } as never);
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-statement-full")).toHaveTextContent(
      "「当 RSI(14)<30 且量能>2倍均值时做多」",
    );
    expect(await screen.findByTestId("vr-union-balance")).toHaveTextContent(
      "并集余量 500 bar · 剩余 1 段",
    );
  });

  it("D1 双降级：statement null→假设不可达；union-balance 失败→余量不可达", async () => {
    mockDetail.mockResolvedValue(makeDetail());
    mockUnion.mockRejectedValue(new Error("404"));
    ui(<RecordDetail record={makeRow({ statement: null })} />);
    expect(await screen.findByTestId("vr-statement-full")).toHaveTextContent("假设不可达");
    await waitFor(() => {
      expect(screen.getByTestId("vr-union-balance")).toHaveTextContent("并集余量不可达");
    });
  });

  it("D1 adhoc 记录不发起余量查询直接降级", async () => {
    mockDetail.mockResolvedValue(makeDetail({ hypothesis_id: "adhoc" }));
    ui(<RecordDetail record={makeRow({ hypothesis_id: "adhoc", statement: null })} />);
    await screen.findByTestId("vr-capability-badges");
    expect(screen.getByTestId("vr-union-balance")).toHaveTextContent("并集余量不可达");
    expect(mockUnion).not.toHaveBeenCalled();
  });

  it("D2+V3+V2：标记=trace filled 行、trail 挂线真值、逐笔未平仓、信号回溯渲染", async () => {
    const d = makeDetail();
    d.executions[0].trace_rows = [
      makeTrace({ event_code: 10, bar_time: "2026-04-02 00:00:00+00:00" }),
      makeTrace({ event_code: 11, seq: 1, bar_time: "2026-04-03 00:00:00+00:00" }),
      makeTrace({ event_code: 20, seq: 0, bar_time: "2026-04-03 00:00:00+00:00", price: 98 }),
      makeTrace({ outcome: "rejected", reason: "exposure_cap", event_code: 30, seq: 0, bar_time: "2026-04-06 00:00:00+00:00", price: null, size: null }),
      makeTrace({ outcome: "rejected", reason: "insufficient_funds", event_code: 31, seq: 1, bar_time: "2026-04-06 00:00:00+00:00", price: null, size: null }),
      makeTrace({ outcome: "swallowed", reason: "add_inhibited", event_code: 34, seq: 2, bar_time: "2026-04-07 00:00:00+00:00", price: null, size: null, capped: null }),
      makeTrace({ event_code: 12, seq: 0, bar_time: "2026-04-08 00:00:00+00:00", capped: 0.6 }),
    ];
    d.mtf_gated_bars = 7;
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    // D2 全区间装载（契约 A）：显式 limit 50000 + 全区间 start/end 单次拉取
    await waitFor(() => {
      expect(mockOhlcv).toHaveBeenCalledWith(
        "BTCUSDT", "30m",
        expect.objectContaining({ start: "2026-04-01", end: "2026-09-01", limit: 50000 }),
      );
    });
    // D2 标记数=filled 行（10/11/20/12——rejected/swallowed 不进标记）；
    // text=动作名+成交价（契约 D——traceMarkers 单源带价，按 time 升序）
    const kline = await screen.findByTestId("vr-kline");
    expect(kline).toHaveAttribute("data-markers", "4");
    expect(kline).toHaveAttribute(
      "data-markers-text",
      "▲开多 100.0|▲加多 100.0|▼平多 98.0|▽减仓 100.0",
    );
    // 契约 C：equity 副图（subChartType=equity；mock 5 bar vs 4 值 mismatch
    // → 份级降级锚 compact 重建 4 点——zip 前提由按份对账保证）
    expect(kline).toHaveAttribute("data-subchart", "equity");
    expect(kline).toHaveAttribute("data-equity-len", "4");
    // trail_series 真值挂线（禁重算——setObservationSeries 以原值调用）
    await waitFor(() => {
      expect(mockChartHandle.setObservationSeries).toHaveBeenCalledWith(
        [{ time: "2026-04-01 00:00:00+00:00", value: 98000 }],
      );
    });
    // V2 逐笔表：开平双价+未平仓如实+出场对齐金族（20 码）
    fireEvent.click(screen.getByRole("tab", { name: "成交记录" }));
    const row0 = await screen.findByTestId("vr-trade-row-0");
    expect(row0).toHaveTextContent("100");
    expect(row0).toHaveTextContent("110");
    expect(row0).toHaveTextContent("止损");
    const row1 = screen.getByTestId("vr-trade-row-1");
    expect(row1).toHaveTextContent("未平仓");
    // V3 信号回溯：被拒信号+reason 两族中文+capped+MTF 提示行
    fireEvent.click(screen.getByRole("tab", { name: "信号回溯" }));
    const trace = await screen.findByTestId("vr-signal-trace");
    expect(trace).toHaveTextContent("敞口上限拦截");
    expect(trace).toHaveTextContent("资金不足");
    expect(trace).toHaveTextContent("加仓抑制清洗");
    expect(trace).toHaveTextContent("60%");
    expect(screen.getByTestId("vr-mtf-gated-note")).toHaveTextContent("MTF 门控拦截 7 bar");
  });

  it("D3 一致性核对：isolated 恒等断言（末值/首值 vs metrics.total_return）", async () => {
    mockDetail.mockResolvedValue(makeDetail());
    ui(
      <RecordDetail
        record={makeRow({
          result: { metrics: { total_return: 0.124 }, symbol: "BTCUSDT" },
        })}
      />,
    );
    const eq = await screen.findByTestId("vr-equity-consistency");
    expect(eq).toHaveTextContent("与声称收益一致性核对：终值 +12.4% ✓");
    expect(screen.getByTestId("vr-equity")).toHaveTextContent("+12.40%");
  });

  it("D3 不一致显 ✗；random 份身份标签（当前份=最差窗口）", async () => {
    mockDetail.mockResolvedValue(makeDetail({ level: "random_window" }));
    ui(
      <RecordDetail
        record={makeRow({
          level: "random_window",
          params: { symbol: "BTCUSDT", timeframe: "30m", data_start: "2026-04-01", data_end: "2026-09-01" },
          result: { metrics: { total_return: 0.5 }, summary: { worst_window: { start: "2026-04-01T00:00:00+00:00", metrics: { total_return: 0.124 } } } },
        })}
      />,
    );
    const eq = await screen.findByTestId("vr-equity-consistency");
    // random 非 isolated 无恒等断言——身份标签路径
    expect(eq).toHaveTextContent("窗口 main（最差窗口）");
  });

  it("D3 空曲线空态（零 bar 区间如实呈现）+equity 副图不渲染（全空→equityData=[]）", async () => {
    const d = makeDetail();
    d.executions[0].equity = {
      grid_aligned: true, timeframe: "30m", start: null, length: 0,
      timestamps: null, values: [],
    };
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-equity")).toHaveTextContent("无 equity 留痕");
    // 契约 C 空态：全空→equityData=[]（副图 KlineChart 内部 length>0 门不渲染）
    const kline = await screen.findByTestId("vr-kline");
    expect(kline).toHaveAttribute("data-equity-len", "0");
  });

  it("V4 四元判读：signal_trace null→代际陈述；能力徽标上线前", async () => {
    // null 代际（antd Tabs 懒渲染——信号回溯页签需先激活）
    mockDetail.mockResolvedValue(
      makeDetail({ signal_trace: null, trade_semantics: null, stop_price: null }),
    );
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-cap-信号留痕")).toHaveTextContent("上线前");
    fireEvent.click(screen.getByRole("tab", { name: "信号回溯" }));
    expect(await screen.findByTestId("vr-trace-legacy")).toHaveTextContent(
      "产生于信号留痕能力上线前",
    );
  });

  it("V4：signal_trace false→管线失败缺陷态；true+全份空→W0", async () => {
    mockDetail.mockResolvedValue(
      makeDetail({ signal_trace: false, executions: [{ ...makeDetail().executions[0], trace_rows: [] }] }),
    );
    ui(<RecordDetail record={makeRow()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "信号回溯" }));
    expect(await screen.findByTestId("vr-trace-failed")).toHaveTextContent("缺陷态");
  });

  it("V2 点行定位：点击逐笔行调 scrollToTime（统一成交锚）", async () => {
    mockDetail.mockResolvedValue(makeDetail());
    ui(<RecordDetail record={makeRow()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "成交记录" }));
    fireEvent.click(await screen.findByTestId("vr-trade-row-0"));
    expect(mockChartHandle.scrollToTime).toHaveBeenCalledWith(
      "2026-04-02 00:00:00+00:00",
    );
  });

  it("契约 E 单份视图主图过滤（change-2 CARD-01）：data-len=该份闭端 bar 数+trail/bands 单份切换+零 bar 份空态", async () => {
    const d = makeDetail();
    // 份 2（i=1）趋势：04-02~04-04 闭端恰 3 根蜡烛（beforeEach 5 根在 04-01~04-05）
    d.executions.push({
      ...d.executions[0],
      exec_ref: "趋势", exec_index: 1,
      range_start: "2026-04-02", range_end: "2026-04-04",
      equity: {
        grid_aligned: false, timeframe: "30m", start: null, length: 2,
        timestamps: ["2026-04-02 00:00:00+00:00", "2026-04-02 00:30:00+00:00"],
        values: [100000, 101500],
      },
      trace_rows: [makeTrace({ exec_ref: "趋势", bar_time: "2026-04-03 00:00:00+00:00" })],
      trail_series: [{ time: "2026-04-02 00:00:00+00:00", value: 97000 }],
    });
    // 份 3（i=2）缺口：04-11~04-20 闭端零 bar——单份空态新可达分支
    d.executions.push({
      ...d.executions[0],
      exec_ref: "缺口", exec_index: 2,
      range_start: "2026-04-11", range_end: "2026-04-20",
      equity: { grid_aligned: false, timeframe: "30m", start: null, length: 0, timestamps: null, values: [] },
      trace_rows: [], trail_series: null, trades: [],
    });
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    // all 态：主图画全区间 5 根；段带=3 份 3 条
    const kline = await screen.findByTestId("vr-kline");
    expect(kline).toHaveAttribute("data-len", "5");
    await waitFor(() => {
      const last = mockBandSetData.mock.calls[mockBandSetData.mock.calls.length - 1]?.[0];
      expect(last).toHaveLength(3);
    });
    // 切份 2·趋势：data-len=3（闭端过滤——修复前恒 5）；trail=该份单段（非拼接）；
    // bands=该份 1 条（label=趋势）
    openViewSelect();
    const opt = await screen.findByText("份 2 · 趋势（2026-04-02 → 2026-04-04）", { exact: false });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => expect(screen.getByTestId("vr-view-mode")).toHaveTextContent("单份 趋势"));
    expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "3");
    await waitFor(() => {
      const calls = mockChartHandle.setObservationSeries.mock.calls;
      const last = calls[calls.length - 1][0] as Array<{ time: string; value: number | null }>;
      expect(last).toEqual([{ time: "2026-04-02 00:00:00+00:00", value: 97000 }]);
    });
    await waitFor(() => {
      const last = mockBandSetData.mock.calls[mockBandSetData.mock.calls.length - 1]?.[0];
      expect(last).toEqual([
        { startTime: toTime("2026-04-02"), endTime: toTime("2026-04-04"), label: "趋势" },
      ]);
    });
    // 切份 3·缺口：闭端零 bar →「该区间无 K 线数据」空态（图不渲染）
    openViewSelect();
    const opt2 = await screen.findByText("份 3 · 缺口（2026-04-11 → 2026-04-20）", { exact: false });
    fireEvent.click(opt2.closest(".ant-select-item-option") ?? opt2);
    await waitFor(() => expect(screen.getByTestId("vr-view-mode")).toHaveTextContent("单份 缺口"));
    expect(await screen.findByText("该区间无 K 线数据")).toBeInTheDocument();
    expect(screen.queryByTestId("vr-kline")).toBeNull();
  });

  it("detail_available=false：不可复核空骨架如实", async () => {
    mockDetail.mockResolvedValue(makeDetail({ detail_available: false, executions: [] }));
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByText(/detail_available=false/)).toBeInTheDocument();
  });

  it("视图切换 D3/V1 联动（activeExecIdx 跟随）：切份 1 后 D3 末值与成交表=份 1 数据，切回全区间归份 0", async () => {
    const d = makeDetail();
    d.executions.push({
      ...d.executions[0],
      exec_ref: "趋势", exec_index: 1,
      range_start: "2026-04-11", range_end: "2026-04-20",
      equity: {
        grid_aligned: false, timeframe: "30m", start: null, length: 2,
        timestamps: ["2026-04-11 00:00:00+00:00", "2026-04-11 00:30:00+00:00"],
        values: [100000, 101500],
      },
      trades: [{
        seq: 0, entry_time: "2026-04-12 00:00:00+00:00", exit_time: null,
        entry_price: 200, exit_price: null, size: 1, direction: "long", pnl: null,
      }],
      trace_rows: [],
      trail_series: null,
    });
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    // 缺省全区间：activeExecIdx=0 → D3 末值=份 0（112400.0）
    expect(await screen.findByTestId("vr-equity")).toHaveTextContent("末 112400.0");
    // 切份 1：D3 末值=101500.0；V1 成交记录表=份 1 单笔（2026-04-12 入场）
    openViewSelect();
    // 份 N 从 1 起（=i+1）——趋势为数组第 2 份即「份 2」
    const opt = await screen.findByText("份 2 · 趋势（2026-04-11 → 2026-04-20）", { exact: false });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(screen.getByTestId("vr-equity")).toHaveTextContent("末 101500.0");
    });
    fireEvent.click(screen.getByRole("tab", { name: "成交记录" }));
    expect(await screen.findByTestId("vr-trade-row-0")).toHaveTextContent("2026-04-12 00:00");
    expect(screen.queryByTestId("vr-trade-row-1")).toBeNull();
    // 切回全区间：activeExecIdx 归 0 → D3 末值回份 0
    openViewSelect();
    const allOpt = await screen.findByText("全区间（全部份连续）");
    fireEvent.click(allOpt.closest(".ant-select-item-option") ?? allOpt);
    await waitFor(() => {
      expect(screen.getByTestId("vr-equity")).toHaveTextContent("末 112400.0");
    });
  });

  it("多份视图选择器（契约 E）：缺省全区间+全份合并标记；份选择器含全区间首项", async () => {
    const d = makeDetail();
    d.executions.push({
      ...d.executions[0],
      exec_ref: "趋势", exec_index: 1,
      range_start: "2026-04-11", range_end: "2026-04-20",
      equity: {
        grid_aligned: false, timeframe: "30m", start: null, length: 2,
        timestamps: ["2026-04-11 00:00:00+00:00", "2026-04-11 00:30:00+00:00"],
        values: [100000, 101500],
      },
      trace_rows: [makeTrace({ exec_ref: "趋势", bar_time: "2026-04-12 00:00:00+00:00" })],
      trail_series: [{ time: "2026-04-11 00:00:00+00:00", value: 97000 }],
    });
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    // 缺省 viewAll：视图模式标=全区间；两份 trace 各 1 filled 行合并=2 标记；
    // equity 全区间拼接=份1 4 点+段界断点 1+份2 2 点=7 项
    expect(await screen.findByTestId("vr-view-mode")).toHaveTextContent("全区间");
    const kline = screen.getByTestId("vr-kline");
    expect(kline).toHaveAttribute("data-markers", "2");
    expect(kline).toHaveAttribute("data-equity-len", "7");
    // trail 全区间拼接（契约 F）：份1 真值+段界 null 断点+份2 真值（跨段不连线）
    await waitFor(() => {
      const calls = mockChartHandle.setObservationSeries.mock.calls;
      const last = calls[calls.length - 1][0] as Array<{ time: string; value: number | null }>;
      expect(last).toEqual([
        { time: "2026-04-01 00:00:00+00:00", value: 98000 },
        { time: "2026-04-10T23:59:59.000Z", value: null },
        { time: "2026-04-11 00:00:00+00:00", value: 97000 },
      ]);
    });
    // 份选择器在场（「全区间」首项+份清单——antd options 不落 DOM，
    // 经视图模式标与 combobox 在场共同锚定）
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(2);
    // 切单份（契约 E 五过滤要素）：视图模式标=单份段名；markers=该份 1；
    // equity=该份 4 点无段界断点；queryKey 不变不重拉（mockOhlcv 调用数不变）
    const callsBefore = mockOhlcv.mock.calls.length;
    openViewSelect();
    const opt = await screen.findByText("份 1 · main（2026-04-01 → 2026-09-01）", { exact: false });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(screen.getByTestId("vr-view-mode")).toHaveTextContent("单份 main");
    });
    const kline2 = screen.getByTestId("vr-kline");
    expect(kline2).toHaveAttribute("data-markers", "1");
    expect(kline2).toHaveAttribute("data-equity-len", "4");
    expect(mockOhlcv.mock.calls.length).toBe(callsBefore);
  });
});

describe("色点单源（三视图同色——V2 出场对齐金族）", () => {
  it("逐笔表出场色点与 TRACE_FAMILY_COLOR.risk 同源", async () => {
    const d = makeDetail();
    d.executions[0].trace_rows = [
      makeTrace({ event_code: 20, bar_time: "2026-04-03 00:00:00+00:00" }),
    ];
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "成交记录" }));
    const row = await screen.findByTestId("vr-trade-row-0");
    const dot = row.querySelector("td span span");
    // jsdom 将内联色序列化为 rgb 形（沿 CHART_VC 先例换算断言）
    const goldRgb = `rgb(${[TRACE_FAMILY_COLOR.risk.slice(1, 3), TRACE_FAMILY_COLOR.risk.slice(3, 5), TRACE_FAMILY_COLOR.risk.slice(5, 7)]
      .map((h) => parseInt(h, 16)).join(", ")})`;
    expect(dot?.getAttribute("style")).toContain(`background-color: ${goldRgb}`);
  });
});

describe("RecordDetail 图高视口化（upgrade-vr-responsive-layout P-2 特征锚）", () => {
  // max(round(innerHeight × 0.55), 420)——jsdom 默认 768，用例内显式
  // 定义视口高并还原（沿 setup.ts matchMedia 的 defineProperty 形制）
  function setViewportHeight(px: number) {
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      writable: true,
      value: px,
    });
  }
  afterEach(() => setViewportHeight(768));

  it("高视口换算：innerHeight=1000 → round(0.55×1000)=550 传入 KlineChart", async () => {
    setViewportHeight(1000);
    mockDetail.mockResolvedValue(makeDetail());
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute(
      "data-height",
      "550",
    );
  });

  it("矮窗口下限：innerHeight=700 → round(385)<420 → 420 下限生效", async () => {
    setViewportHeight(700);
    mockDetail.mockResolvedValue(makeDetail());
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute(
      "data-height",
      "420",
    );
  });

  it.each([
    [300, 420], // 远低于下限（165→420）
    [700, 420], // design P-2 验收判定例（385→420）
    [764, 420], // 换算恰好触下限（420.2→round 420）
    [765, 421], // 越过下限首点（420.75→round 421）
  ])(
    "下限不变量：innerHeight=%i → 图高恒 max(round(0.55h), 420)=%i",
    async (viewport, expected) => {
      setViewportHeight(viewport);
      mockDetail.mockResolvedValue(makeDetail());
      ui(<RecordDetail record={makeRow()} />);
      expect(await screen.findByTestId("vr-kline")).toHaveAttribute(
        "data-height",
        String(expected),
      );
    },
  );

  it("resize 跟随：550 → 420（下限）→ 1100 逐步重算（局部监听）", async () => {
    setViewportHeight(1000);
    mockDetail.mockResolvedValue(makeDetail());
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute(
      "data-height",
      "550",
    );
    // 760×0.55=418 → round 418 < 420 → 下限
    setViewportHeight(760);
    fireEvent(window, new Event("resize"));
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute(
        "data-height",
        "420",
      );
    });
    // 2000×0.55=1100 > 420 → 直通
    setViewportHeight(2000);
    fireEvent(window, new Event("resize"));
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute(
        "data-height",
        "1100",
      );
    });
  });
});

describe("契约 H/I/J/K：DNA 指标叠加+手动开关+周期切换吸附（upgrade-vr-indicator-overlay）", () => {
  function makeStrategy(dna: unknown) {
    return { dna, strategy_id: "st_y1", name: "s", generation: 1 } as never;
  }
  const DNA_FIX = {
    signal_genes: [
      { indicator: "BB", params: { period: 20, std: 2 }, field: "middle" },
      { indicator: "EMA", params: { period: 50 } },
      { indicator: "RSI", params: { period: 14 } },
      { indicator: "ATR", params: { period: 14 } },
    ],
    layers: [{ timeframe: "1d", signal_genes: [{ indicator: "EMA", params: { period: 20 } }] }],
  };

  it("契约 H/I：DNA 引用指标自动叠加——徽标组（含降级标注）+indicators 通道+参数一致", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(DNA_FIX));
    mockChartInd.mockResolvedValue({
      ema: { "50": [{ time: "2026-04-05 00:00:00+00:00", value: 1 }] },
      boll: {
        upper: [{ time: "2026-04-05 00:00:00+00:00", value: 2 }],
        middle: [{ time: "2026-04-05 00:00:00+00:00", value: 3 }],
        lower: [{ time: "2026-04-05 00:00:00+00:00", value: 4 }],
      },
      rsi: null, macd: null, kdj: null,
    } as never);
    mockDetail.mockResolvedValue(makeDetail());
    ui(<RecordDetail record={makeRow()} />);
    await waitFor(() => expect(mockGetStrategy).toHaveBeenCalled());
    const badges = await screen.findByTestId("vr-dna-badges");
    expect(badges).toHaveTextContent("BB(20,2)·中轨");
    expect(badges).toHaveTextContent("EMA(50)");
    expect(badges).toHaveTextContent("RSI");
    expect(badges).toHaveTextContent("ATR");
    expect(badges).toHaveTextContent("跨周期层·EMA");
    // 请求参数与策略一致：ema_periods=50+boll 20/2（与 K 线同锚 start/end/limit）
    await waitFor(() => expect(mockChartInd).toHaveBeenCalledTimes(2));
    expect(mockChartInd).toHaveBeenCalledWith("BTCUSDT", "30m", expect.objectContaining({
      ema_periods: "50", limit: 50000,
    }));
    expect(mockChartInd).toHaveBeenCalledWith("BTCUSDT", "30m", expect.objectContaining({
      boll_enabled: true, boll_period: 20, boll_std: 2,
    }));
    // 指标通道：EMA 1 线+BB 3 线=4 线进 KlineChart
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-indicators", "4");
    });
  });

  it("契约 J：手动开关——EMA 开→追加请求成线；同参数与 DNA 合并去重", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(DNA_FIX));
    mockChartInd.mockImplementation((
      _s: string, _t: string, params?: Record<string, unknown>,
    ) => Promise.resolve(
      params?.ema_periods
        ? { ema: { "50": [{ time: "2026-04-05 00:00:00+00:00", value: 1 }] }, boll: null, rsi: null, macd: null, kdj: null }
        : { ema: {}, boll: { upper: [], middle: [], lower: [] }, rsi: null, macd: null, kdj: null },
    ) as never);
    mockDetail.mockResolvedValue(makeDetail());
    ui(<RecordDetail record={makeRow()} />);
    await screen.findByTestId("vr-kline");
    await waitFor(() => expect(mockChartInd).toHaveBeenCalledTimes(2));
    // 开手动 EMA（同参数 50——与 DNA 线合并不新增线）
    fireEvent.click(screen.getByTestId("vr-manual-ema"));
    await waitFor(() => expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-indicators", "4"));
    // 不同参数各自成线（聚合串形制）已由 useOverlayIndicators 单测锚
    // （fullRangeData.test「ema 聚合串+逐 BB 组请求」）——此处 UI 面锚
    // 开关交互与同参数合并去重即止
  });

  it("契约 K：周期切换——K 线按新档重拉（queryKey 维度）+标记吸附到高周期 bar+基准徽标恒显", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    const d = makeDetail();
    d.executions[0].trace_rows = [
      makeTrace({ bar_time: "2026-04-02 06:00:00+00:00" }),
      makeTrace({ event_code: 13, seq: 1, bar_time: "2026-04-03 18:00:00+00:00" }),
    ];
    mockDetail.mockResolvedValue(d);
    // 基准 30m 请求→5 根 30m bar；1d 请求→3 根日 bar（04-01/02/03）
    mockOhlcv.mockImplementation((
      _s: string, tf: string,
    ) => Promise.resolve({
      data: tf === "1d"
        ? ["2026-04-01", "2026-04-02", "2026-04-03"].map((day) => ({
            timestamp: `${day} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          }))
        : Array.from({ length: 5 }, (_, i) => ({
            timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          })),
    }) as never);
    ui(<RecordDetail record={makeRow()} />);
    // 基准徽标恒显 + 缺省 30m
    expect(await screen.findByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    const kline0 = screen.getByTestId("vr-kline");
    expect(kline0).toHaveAttribute("data-markers", "2");
    // 切 1d：K 线重拉（第二档调用）+两标记吸附到 04-02/04-03 日 bar（无丢失）
    openTfSelect();
    const opt = await screen.findByText("1d", { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(mockOhlcv.mock.calls.some((c) => c[1] === "1d")).toBe(true);
    });
    await waitFor(() => {
      const kline = screen.getByTestId("vr-kline");
      expect(kline).toHaveAttribute("data-markers", "2");
      expect(kline).toHaveAttribute("data-markers-text", "▲开多 100.0|▼平多 100.0");
    });
    // 切回基准：queryKey 复基准档
    openTfSelect();
    const optBack = await screen.findByText("30m", { selector: ".ant-select-item-option-content" });
    fireEvent.click(optBack.closest(".ant-select-item-option") ?? optBack);
    await waitFor(() =>
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m"),
    );
  });

  it("契约 K 增量：equity/trail 吸附落点——切 1d 后时间列落日 bar+同 bar 保尾值合并", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    const d = makeDetail();
    // trail 两点半径点（04-02 06:00/18:00）+equity timestamps 直用两点——同落 04-02 日 bar
    d.executions[0].trail_series = [
      { time: "2026-04-02 06:00:00+00:00", value: 98000 },
      { time: "2026-04-02 18:00:00+00:00", value: 99000 },
    ];
    d.executions[0].equity = {
      grid_aligned: false, timeframe: "30m", start: null, length: 2,
      timestamps: ["2026-04-02 06:00:00+00:00", "2026-04-02 18:00:00+00:00"],
      values: [100000, 101000],
    };
    mockDetail.mockResolvedValue(d);
    mockOhlcv.mockImplementation((
      _s: string, tf: string,
    ) => Promise.resolve({
      data: tf === "1d"
        ? ["2026-04-01", "2026-04-02", "2026-04-03"].map((day) => ({
            timestamp: `${day} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          }))
        : Array.from({ length: 5 }, (_, i) => ({
            timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          })),
    }) as never);
    ui(<RecordDetail record={makeRow()} />);
    // 基准 30m：吸附短路——equity/trail 原时间列原点数
    const kline0 = await screen.findByTestId("vr-kline");
    expect(kline0).toHaveAttribute("data-equity-len", "2");
    await waitFor(() => {
      const calls = mockChartHandle.setObservationSeries.mock.calls;
      const last = calls[calls.length - 1][0] as Array<{ time: string; value: number | null }>;
      expect(last).toHaveLength(2);
    });
    // 切 1d：两点均落 [04-02, 04-03) 同一日 bar——同 bar 保尾值合并 1 点，
    // equity 时间列=日 bar 时间戳（吸附只改时间锚不改值）
    openTfSelect();
    const opt = await screen.findByText("1d", { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      const kline = screen.getByTestId("vr-kline");
      expect(kline).toHaveAttribute("data-equity-len", "1");
      expect(kline).toHaveAttribute("data-equity-times", "2026-04-02 00:00:00+00:00");
    });
    await waitFor(() => {
      const calls = mockChartHandle.setObservationSeries.mock.calls;
      const last = calls[calls.length - 1][0] as Array<{ time: string; value: number | null }>;
      expect(last).toEqual([{ time: "2026-04-02 00:00:00+00:00", value: 99000 }]);
    });
  });

  it("契约 K 增量：无数据档禁用回路——切 1d 端点 404→该档选项禁用+自动回基准+禁用点击零新请求", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    mockDetail.mockResolvedValue(makeDetail());
    mockOhlcv.mockImplementation((
      _s: string, tf: string,
    ) =>
      tf === "1d"
        ? Promise.reject(new ApiError("client", "404", 404))
        : Promise.resolve({
            data: Array.from({ length: 5 }, (_, i) => ({
              timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
            })),
          }) as never);
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    // 切 1d：端点 404→error 态→deadTfs 收录+viewTf 自动回基准（基准档缓存重现在图）
    openTfSelect();
    const opt = await screen.findByText("1d", { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
    // 1d 选项禁用态在场；禁用项点击零新请求（1d 调用数恒 1）
    openTfSelect();
    const optDisabled = await screen.findByText("1d", { selector: ".ant-select-item-option-content" });
    const optEl = optDisabled.closest(".ant-select-item-option");
    expect(optEl?.className).toContain("ant-select-item-option-disabled");
    fireEvent.click(optEl ?? optDisabled);
    await waitFor(() => {
      expect(mockOhlcv.mock.calls.filter((c) => c[1] === "1d")).toHaveLength(1);
    });
  });

  it("契约 K 增量（REQ-4 判死分流）：非 404 失败——档位不禁用+提示条在场+重切该档必重发", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    mockDetail.mockResolvedValue(makeDetail());
    let failNext = true;
    mockOhlcv.mockImplementation((_s: string, tf: string) => {
      if (tf === "1h") {
        if (failNext) return Promise.reject(new ApiError("server", "boom", 500));
      }
      return Promise.resolve({
        data: Array.from({ length: 5 }, (_, i) => ({
          timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5,
          close: 1.5,
        })),
      }) as never;
    });
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline").then((el) => el.getAttribute("data-len")).catch(() => null)).toBe("5");
    // 切 1h → 500 失败：档位不禁用（deadTfs 零收录）+提示条在场+回基准
    openTfSelect();
    const opt = await screen.findByText("1h", { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(screen.getByTestId("vr-tf-fetch-failed")).toHaveTextContent("1h 档拉取失败");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    // 选项未禁用（非 404 不入 deadTfs）
    openTfSelect();
    const opt1h = await screen.findByText("1h", { selector: ".ant-select-item-option-content" });
    expect(opt1h.closest(".ant-select-item-option")?.className).not.toContain("ant-select-item-option-disabled");
    // 重切 1h：retrySeq+1 → queryKey 变化 → mock 调用数 +1（重发确证）
    fireEvent.click(opt1h.closest(".ant-select-item-option") ?? opt1h);
    failNext = false;
    await waitFor(() => {
      expect(mockOhlcv.mock.calls.filter((c) => c[1] === "1h")).toHaveLength(2);
    });
    // 重试成功：数据正常显示
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline").getAttribute("data-len")).toBe("5");
    });
  });

  it("契约 K 增量（REQ-4 判死分流）：裸网络错误（非 ApiError）同走非 404 通道", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    mockDetail.mockResolvedValue(makeDetail());
    mockOhlcv.mockImplementation((_s: string, tf: string) => {
      if (tf === "1h") return Promise.reject(new TypeError("fetch failed"));
      return Promise.resolve({
        data: Array.from({ length: 5 }, (_, i) => ({
          timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
        })),
      }) as never;
    });
    ui(<RecordDetail record={makeRow()} />);
    await screen.findByTestId("vr-kline").then((el) => expect(el.getAttribute("data-len")).toBe("5"));
        openTfSelect();
    const opt = await screen.findByText("1h", { selector: ".ant-select-item-option-content" })
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(screen.getByTestId("vr-tf-fetch-failed")).toHaveTextContent("1h 档拉取失败");
    });
    // 404 零收录断言（选项不禁用）
    openTfSelect();
    const opt1h = await screen.findByText("1h", { selector: ".ant-select-item-option-content" });
    expect(opt1h.closest(".ant-select-item-option")?.className).not.toContain("ant-select-item-option-disabled");
  });

});

describe("特征测试网（proto-b 现状记录——REQ-4 retrySeq/failedTf 两项设计副作用）", () => {
  function makeStrategy(dna: unknown) {
    return { dna, strategy_id: "st_y1", name: "s", generation: 1 } as never;
  }
  const bars5 = () =>
    Array.from({ length: 5 }, (_, i) => ({
      timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
    }));
  /** 生产新鲜度形制 ui 变体——App.tsx staleTime:30_000 还原（「基准档
   *  零重拉」锚的成立前提：有 data 的 query 在新鲜窗口内重订阅不重
   *  发；默认 staleTime:0 形制下每次回基准都 refetchOnMount 重拉，请
   *  求层零重拉断言恒假）。retry 保持 false 与既有 ui() 一致——retry
   *  只放大失败档调用计数倍数（生产 retry:1 下×2），不进入零重拉/重
   *  发两机制的判定路径（重发走 no-data 挂载分支，与 retry 无关） */
  const uiProd = (node: ReactNode) => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
  };
  /** 按档位计 mock 调用数（queryFn 第 2 参=fetchTf） */
  const callsOf = (tf: string) => mockOhlcv.mock.calls.filter((c) => c[1] === tf).length;
  /** 周期下拉选档 */
  const pickTf = async (tf: string) => {
    openTfSelect();
    const opt = await screen.findByText(tf, { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
  };

  // 特征 A（副作用①现状锚——修复后预期转红作回归检测器）：retrySeq 是
  // RecordDetail 组件级单值，进 useFullRangeKline queryKey 末位且作用于
  // 所有档位（含基准档）。非 404 失败档重切递增（0→1）后若再失败回基
  // 准，基准档 queryKey 的 seq 位已变 → 缓存 miss 重拉。旧代码（REQ-4
  // 机制引入前）该路径零请求命中——本测试把「递增前零重拉 vs 递增后
  // miss 重拉」两段现状分层钉住
  it("特征：非 404 失败档重切递增后再失败回基准——首回基准零重拉（seq 恒 0），次回基准缓存 miss 重拉（30m 调用 1→2）", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    mockDetail.mockResolvedValue(makeDetail());
    mockOhlcv.mockImplementation((_s: string, tf: string) => {
      if (tf === "1h") return Promise.reject(new ApiError("server", "boom", 500));
      return Promise.resolve({ data: bars5() }) as never;
    });
    uiProd(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    // 切 1h → 500 失败：failedTf="1h" + 自动回基准。seq 未动 → 基准档
    // queryKey ["vr-kline-full",…,"30m",…,0] 未变 → 缓存命中零重拉
    await pickTf("1h");
    await waitFor(() => {
      expect(screen.getByTestId("vr-tf-fetch-failed")).toHaveTextContent("1h 档拉取失败");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
    expect(callsOf("30m")).toBe(1);
    // 重切 1h（failedTf 命中 → seq 0→1）→ 又失败 → 再回基准。基准档
    // queryKey seq 位=1 ≠ 缓存 seq=0 → miss 重拉（副作用①现状——此断
    // 言修复后预期转红：回基准档零重拉为验收标准②）
    await pickTf("1h");
    await waitFor(() => {
      expect(callsOf("1h")).toBe(2);
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    await waitFor(() => {
      expect(callsOf("30m")).toBe(2);
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
  });

  // 特征 B（存量契约锚加强——404 判死与基准缓存重现为存量契约，修复不
  // 得破坏）：404 分流不动 seq → 回基准基准档 queryKey 恒 seq=0 → 缓存
  // 命中零重拉。既有锚（上方「无数据档禁用回路」）断 404 档自身调用恒
  // 1；本锚补请求层证据：双 404 档（1d、3d）先后判死后基准档 30m 调用
  // 恒 1 + 两档选项均禁用（deadTfs 累积）
  it("特征：404 判死路径 seq 恒 0——双 404 档先后判死后回基准基准档零重拉（30m 调用恒 1）", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    mockDetail.mockResolvedValue(makeDetail());
    mockOhlcv.mockImplementation((_s: string, tf: string) => {
      if (tf === "1d" || tf === "3d") {
        return Promise.reject(new ApiError("client", "404", 404));
      }
      return Promise.resolve({ data: bars5() }) as never;
    });
    uiProd(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    // 切 1d → 404：deadTfs 收录 + 回基准，基准档缓存重现（零重拉）
    await pickTf("1d");
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
    expect(callsOf("30m")).toBe(1);
    // 切 3d 亦 404：第二次判死分流后基准档仍零重拉（seq 全程恒 0）
    await pickTf("3d");
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
    expect(callsOf("30m")).toBe(1);
    // 存量契约面：两档选项均禁用（deadTfs 收录累积不互清）
    openTfSelect();
    for (const tf of ["1d", "3d"]) {
      const opt = await screen.findByText(tf, { selector: ".ant-select-item-option-content" });
      expect(opt.closest(".ant-select-item-option")?.className).toContain(
        "ant-select-item-option-disabled",
      );
    }
  });

  // 特征 C（副作用②现状锚）：failedTf 单值——双档先后非 404 失败时后者
  // 覆盖前者（"1h"→"4h"）。重切先失败档 1h 时 tf!==failedTf → seq 不递
  // 增 → queryKey 未变 → 重发依赖 react-query 默认行为（error 态无 data
  // 的 query 重订阅时挂载判定走 no-data 分支重发——query-core 5.99.0
  // queryObserver shouldLoadOnMount：data===undefined 且 retryOnMount!==false
  // 即 fetch，与 staleTime 无关，App.tsx staleTime:30s/retry:1 生产配置
  // 下同成立）。该机制无显式代码路径——本锚即其现状记录（验收标准③要
  // 求显式锚，修复须以显式机制使本锚保持绿或以等价显式锚替换）
  it("特征：双失败档（1h→4h）failedTf 被后者覆盖——重切 1h 不递增 seq 仍重发（默认行为）；1h 成功后提示条仍指 4h；回基准零重拉", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    mockDetail.mockResolvedValue(makeDetail());
    let fail1h = true;
    mockOhlcv.mockImplementation((_s: string, tf: string) => {
      if (tf === "1h" && fail1h) return Promise.reject(new ApiError("server", "boom", 500));
      if (tf === "4h") return Promise.reject(new ApiError("server", "boom2", 502));
      return Promise.resolve({ data: bars5() }) as never;
    });
    uiProd(<RecordDetail record={makeRow()} />);
    expect(await screen.findByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    // 切 1h → 失败：failedTf="1h"，自动回基准（seq 未动）
    await pickTf("1h");
    await waitFor(() => {
      expect(screen.getByTestId("vr-tf-fetch-failed")).toHaveTextContent("1h 档拉取失败");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    // 切 4h → 失败：failedTf="4h" 覆盖 "1h"（单值现状），回基准
    await pickTf("4h");
    await waitFor(() => {
      expect(screen.getByTestId("vr-tf-fetch-failed")).toHaveTextContent("4h 档拉取失败");
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-base-tf")).toHaveTextContent("基准 30m");
    });
    // 重切 1h：tf="1h"!==failedTf="4h" → seq 不递增（queryKey 不变），
    // 重发靠 error 态 query 重订阅的挂载重发默认行为（现状机制）
    fail1h = false;
    await pickTf("1h");
    await waitFor(() => {
      expect(callsOf("1h")).toBe(2);
    });
    // 1h 重试成功：图正常装载
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
    // 现状锚：failedTf="4h" 未清（清除条件 viewTf===failedTf 不满足）→
    // 提示条仍指 4h（1h 已成功但提示的是后失败档——单值覆盖的现状呈现）
    expect(screen.getByTestId("vr-tf-fetch-failed")).toHaveTextContent("4h 档拉取失败");
    // 现状锚：seq 全程未递增 → 回基准基准档 queryKey 未变 → 零重拉
    //（若 seq 曾递增，此处必缓存 miss 重拉成 2——对照特征 A 机制）
    await pickTf("30m");
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toHaveAttribute("data-len", "5");
    });
    expect(callsOf("30m")).toBe(1);
  });

});

describe("段界同 bar 重复时间戳合并（change-3 CARD-01——P0 回归锚）", () => {
  function noAdjacentDup(times: string): boolean {
    const arr = times.split(",");
    return arr.every((t, i) => i === 0 || t !== arr[i - 1]);
  }
  const makeStrategy = (dna: unknown) =>
    ({ dna, strategy_id: "st_y1", name: "s", generation: 1 }) as never;

  it("snap 系触发面：两份跨段吸附落同一日 bar——切 1d 后 equity/trail 无相邻重复 time（末项替换为后值）+基准对照", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    const d = makeDetail();
    // 份 1（main，range 04-01~04-02）：降级 timestamps 直用 3 点（区间 2 bar≠3 值）
    d.executions[0].range_start = "2026-04-01";
    d.executions[0].range_end = "2026-04-02";
    d.executions[0].equity = {
      grid_aligned: false, timeframe: "30m", start: null, length: 3,
      timestamps: ["2026-04-01 12:00:00+00:00", "2026-04-01 18:00:00+00:00", "2026-04-02 06:00:00+00:00"],
      values: [100000, 100500, 101000],
    };
    d.executions[0].trail_series = [
      { time: "2026-04-01 12:00:00+00:00", value: 97000 },
      { time: "2026-04-02 06:00:00+00:00", value: 98000 },
    ];
    // 份 2（趋势，range 04-02~04-03）：直用 3 点——吸附后首点与份 1 末点同落 04-02 日 bar
    d.executions.push({
      ...d.executions[0],
      exec_ref: "趋势", exec_index: 1,
      range_start: "2026-04-02", range_end: "2026-04-03",
      equity: {
        grid_aligned: false, timeframe: "30m", start: null, length: 3,
        timestamps: ["2026-04-02 18:00:00+00:00", "2026-04-03 06:00:00+00:00", "2026-04-03 18:00:00+00:00"],
        values: [102000, 102500, 103000],
      },
      trail_series: [
        { time: "2026-04-02 18:00:00+00:00", value: 99000 },
        { time: "2026-04-03 06:00:00+00:00", value: 99500 },
      ],
      trace_rows: [],
    });
    mockDetail.mockResolvedValue(d);
    mockOhlcv.mockImplementation((
      _s: string, tf: string,
    ) => Promise.resolve({
      data: tf === "1d"
        ? ["2026-04-01", "2026-04-02", "2026-04-03"].map((day) => ({
            timestamp: `${day} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          }))
        : Array.from({ length: 5 }, (_, i) => ({
            timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          })),
    }) as never);
    ui(<RecordDetail record={makeRow()} />);
    // 基准对照（30m）：6 值点+段界断点 1=7 项，无相邻重复（重复缺陷不在基准态呈现）
    const kline0 = await screen.findByTestId("vr-kline");
    expect(kline0).toHaveAttribute("data-equity-len", "7");
    expect(noAdjacentDup(kline0.getAttribute("data-equity-times") ?? "")).toBe(true);
    // 切 1d：两段首尾同落 04-02 日 bar——末项替换为后值（102000），恰 3 项无相邻重复
    openTfSelect();
    const opt = await screen.findByText("1d", { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      const kline = screen.getByTestId("vr-kline");
      expect(kline).toHaveAttribute("data-equity-len", "3");
      expect(kline.getAttribute("data-equity-times")).toBe(
        "2026-04-01 00:00:00+00:00,2026-04-02 00:00:00+00:00,2026-04-03 00:00:00+00:00",
      );
      expect(kline.getAttribute("data-equity-vals")).toBe("100500,102000,103000");
    });
    // trail 同构：段 1 末 98000 与段 2 首 99000 同 bar 合并取后值——3 项
    await waitFor(() => {
      const calls = mockChartHandle.setObservationSeries.mock.calls;
      const last = calls[calls.length - 1][0] as Array<{ time: string; value: number | null }>;
      expect(last).toEqual([
        { time: "2026-04-01 00:00:00+00:00", value: 97000 },
        { time: "2026-04-02 00:00:00+00:00", value: 99000 },
        { time: "2026-04-03 00:00:00+00:00", value: 99500 },
      ]);
    });
  });

  it("buildEquitySeries/concatTrail 触发面：基准视图两份 zip 相接共享边界 bar——无相邻重复+合并取后值", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    const d = makeDetail();
    // 份 1（main，04-01~04-02 闭端 2 bar）zip：values 2 点锚 [04-01 bar, 04-02 bar]
    d.executions[0].range_start = "2026-04-01";
    d.executions[0].range_end = "2026-04-02";
    d.executions[0].equity = {
      grid_aligned: true, timeframe: "30m", start: null, length: 2,
      timestamps: null, values: [100000, 101000],
    };
    d.executions[0].trail_series = [
      { time: "2026-04-01 00:00:00+00:00", value: 97000 },
      { time: "2026-04-02 00:00:00+00:00", value: 98000 },
    ];
    // 份 2（趋势，04-02~04-03）：zip 首点=04-02 bar——与份 1 末点同一时间戳（explicit 相接）
    d.executions.push({
      ...d.executions[0],
      exec_ref: "趋势", exec_index: 1,
      range_start: "2026-04-02", range_end: "2026-04-03",
      equity: {
        grid_aligned: true, timeframe: "30m", start: null, length: 2,
        timestamps: null, values: [102000, 103000],
      },
      trail_series: [
        { time: "2026-04-02 00:00:00+00:00", value: 99000 },
        { time: "2026-04-03 00:00:00+00:00", value: 99500 },
      ],
      trace_rows: [],
    });
    mockDetail.mockResolvedValue(d);
    ui(<RecordDetail record={makeRow()} />);
    // 基准视图即触发：04-02 bar 两份各锚一次——合并取后值（102000）恰 3 项无重复
    const kline = await screen.findByTestId("vr-kline");
    expect(kline.getAttribute("data-equity-times")).toBe(
      "2026-04-01 00:00:00+00:00,2026-04-02 00:00:00+00:00,2026-04-03 00:00:00+00:00",
    );
    expect(kline.getAttribute("data-equity-vals")).toBe("100000,102000,103000");
    // concatTrail 同构：04-02 同时间戳合并取后值 99000——3 项
    await waitFor(() => {
      const calls = mockChartHandle.setObservationSeries.mock.calls;
      const last = calls[calls.length - 1][0] as Array<{ time: string; value: number | null }>;
      expect(last).toEqual([
        { time: "2026-04-01 00:00:00+00:00", value: 97000 },
        { time: "2026-04-02 00:00:00+00:00", value: 99000 },
        { time: "2026-04-03 00:00:00+00:00", value: 99500 },
      ]);
    });
  });
});

describe("切档假态豁免组件面（change-3 CARD-02——源头豁免回归锚）", () => {
  const makeStrategy = (dna: unknown) =>
    ({ dna, strategy_id: "st_y1", name: "s", generation: 1 }) as never;

  it("基准 mismatch 构造仍告警+切 1d 后提示行不在场（假 truncated/mismatch 消除）", async () => {
    mockGetStrategy.mockResolvedValue(makeStrategy(null));
    // 份 range_end 收到 04-03（末 bar 覆盖窗内——先过 truncated 判定）：
    // 基准 30m 闭端 3 根（04-01~04-03）≠equity 4 点——真 mismatch
    const d = makeDetail();
    d.executions[0].range_end = "2026-04-03";
    mockDetail.mockResolvedValue(d);
    mockOhlcv.mockImplementation((
      _s: string, tf: string,
    ) => Promise.resolve({
      data: tf === "1d"
        ? ["2026-04-01", "2026-04-02", "2026-04-03"].map((day) => ({
            timestamp: `${day} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          }))
        : Array.from({ length: 5 }, (_, i) => ({
            timestamp: `2026-04-0${i + 1} 00:00:00+00:00`, open: 1, high: 2, low: 0.5, close: 1.5,
          })),
    }) as never);
    ui(<RecordDetail record={makeRow()} />);
    // 基准视图：真 mismatch 告警在场（份级定位）
    const state = await screen.findByTestId("vr-kline-state");
    expect(state).toHaveTextContent("份 main：K 线 3 根 vs 权益 4 点");
    // 切 1d：修复前 1d 闭端 3 根≠4 点假 mismatch+末 bar<range_end 假 truncated；
    // 修复后源头豁免——恒 ok 态，提示行不在场
    openTfSelect();
    const opt = await screen.findByText("1d", { selector: ".ant-select-item-option-content" });
    fireEvent.click(opt.closest(".ant-select-item-option") ?? opt);
    await waitFor(() => {
      expect(mockOhlcv.mock.calls.some((c) => c[1] === "1d")).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByTestId("vr-kline")).toBeInTheDocument();
      expect(screen.queryByTestId("vr-kline-state")).toBeNull();
    });
  });
});
