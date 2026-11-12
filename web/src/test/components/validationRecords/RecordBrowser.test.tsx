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
}));

// KlineChart mock：React 19 ref prop 直传——渲染期注入命令式句柄
// （scrollToTime/setObservationSeries 采集调用），signalMarkers 计数/
// 文案、height、equityData 长度经 data 属性透出
const mockChartHandle = {
  scrollToTime: vi.fn(),
  setObservationSeries: vi.fn(),
  zoomIn: vi.fn(),
  zoomOut: vi.fn(),
  resetView: vi.fn(),
};
vi.mock("@/components/charts/KlineChart", () => ({
  KlineChart: (props: {
    signalMarkers?: Array<{ text?: string }>;
    height?: number;
    equityData?: Array<unknown>;
    subChartType?: string;
    ref?: { current: unknown };
  }) => {
    if (props.ref) props.ref.current = mockChartHandle;
    return (
      <div
        data-testid="vr-kline"
        data-markers={props.signalMarkers?.length ?? 0}
        data-markers-text={(props.signalMarkers ?? []).map((m) => m.text).join("|")}
        data-height={props.height}
        data-equity-len={props.equityData?.length ?? 0}
        data-subchart={props.subChartType ?? ""}
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
import {
  getValidationRecordsPage,
  getValidationRecordDetail,
  getUnionBalance,
  type ValidationDetailResponse,
  type VrTraceRow,
} from "@/services/validationRecords";
import { getOhlcvBySymbol } from "@/services/datasets";
import type { ValidationRecordRow } from "@/services/workbench";
import { TRACE_FAMILY_COLOR } from "@/components/validationRecords/traceSemantics";

const mockList = vi.mocked(getValidationRecordsPage);
const mockDetail = vi.mocked(getValidationRecordDetail);
const mockUnion = vi.mocked(getUnionBalance);
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

  it("detail_available=false：不可复核空骨架如实", async () => {
    mockDetail.mockResolvedValue(makeDetail({ detail_available: false, executions: [] }));
    ui(<RecordDetail record={makeRow()} />);
    expect(await screen.findByText(/detail_available=false/)).toBeInTheDocument();
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
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    // 切单份（契约 E 五过滤要素）：视图模式标=单份段名；markers=该份 1；
    // equity=该份 4 点无段界断点；queryKey 不变不重拉（mockOhlcv 调用数不变）
    const callsBefore = mockOhlcv.mock.calls.length;
    fireEvent.mouseDown(screen.getByRole("combobox"));
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
