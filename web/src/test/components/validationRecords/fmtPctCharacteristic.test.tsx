/**
 * fmtPct 特征测试网（fix/v2-smoke-fmtpct 前置锚——现状记录语义）。
 * 现状：RecordBrowser.tsx 与 RecordDetail.tsx 各有一处同义私有定义
 * `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`。本文件经两处生产
 * 渲染出口锚定其行为：
 * 出口 A——RecordCards 卡片段 4 total_return 槽（RecordBrowser 形）；
 * 出口 B——vr-equity-consistency 一致性核对行「终值 …」（RecordDetail 形）。
 * 等价性锚：两出口共用同一输入向量与同一期望值表（同输入同输出，
 * 含负数/零/负零边界/进位边界），期望值全部来自重构前 node 直跑两形
 * 制表达式实测（逐值一致）。单源化重构若改变任一形制输出，此处即红。
 * mock 样板复刻自 RecordBrowser.test.tsx（服务层全 mock 零外网红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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

// KlineChart mock：jsdom 不渲染真图——ref 注入命令式句柄、onChartReady
// 同步触发（形制同 RecordBrowser.test.tsx，保 RecordDetail 挂载链）
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

import { RecordCards } from "@/components/validationRecords/RecordBrowser";
import {
  DEFAULT_LIST_FILTERS,
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
  mockUnion.mockResolvedValue({
    hypothesis_id: "hyp_x1", symbol: "BTCUSDT", timeframe: "30m",
    judged: [], remaining_bars: 0, remaining: [], remaining_time: "",
  } as never);
});

/** 特征向量：输入 → 期望输出（重构前 node 直跑两形制表达式实测，逐值一致） */
const FMT_PCT_CASES: Array<{ v: number; out: string; note: string }> = [
  { v: 0.125, out: "+12.5%", note: "正数典型" },
  { v: 0, out: "+0.0%", note: "零值——v>=0 分支带 + 前缀" },
  { v: -0.083, out: "-8.3%", note: "负数——无 + 前缀，负号来自 toFixed" },
  { v: 0.1249, out: "+12.5%", note: "进位边界——toFixed(1) 四舍五入" },
  { v: -0.00004, out: "-0.0%", note: "负零边界——v<0 无前缀且 toFixed 得 -0.0" },
];

describe("fmtPct 特征网·出口 A（RecordBrowser 形——RecordCards 段 4 total_return 槽）", () => {
  for (const c of FMT_PCT_CASES) {
    it(`fmtPct(${c.v}) => "${c.out}"（${c.note}）`, async () => {
      mockList.mockResolvedValue({
        records: [makeRow({
          result: {
            metrics: { total_return: c.v, sharpe_ratio: 2.31, max_drawdown: null, total_trades: 34 },
            symbol: "BTCUSDT",
          },
        })],
        total: 1, offset: 0, limit: 50, has_more: false,
        verdict_counts: { pass: 1, fail: 0, inconclusive: 0 },
        unsortable_count: 0,
      } as never);
      ui(<RecordCards filters={{ ...DEFAULT_LIST_FILTERS }} onSelect={vi.fn()} />);
      const card = await screen.findByTestId("vr-card-rec_test01");
      expect(card).toHaveTextContent(c.out);
    });
  }
});

describe("fmtPct 特征网·出口 B（RecordDetail 形——一致性核对行「终值 …」）", () => {
  for (const c of FMT_PCT_CASES) {
    it(`fmtPct(${c.v}) => "${c.out}"（${c.note}）`, async () => {
      mockDetail.mockResolvedValue(makeDetail());
      ui(
        <RecordDetail
          record={makeRow({
            result: { metrics: { total_return: c.v }, symbol: "BTCUSDT" },
          })}
        />,
      );
      const eq = await screen.findByTestId("vr-equity-consistency");
      // ✓/✗ 尾巴随 claimed 与 equity 重算差漂移（equity 首末固定 100000→112400），
      // 非本锚对象——只锚 fmtPct 产物「终值 <out>」
      expect(eq).toHaveTextContent(`终值 ${c.out}`);
    });
  }
});
