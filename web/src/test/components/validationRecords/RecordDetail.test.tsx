/**
 * RecordDetail 库值污染降级组件测（fix-ohlcv-path-segment-encoding 契约 1
 * 组件层——validation-records-page「库值 symbol/timeframe 值域闸」
 * Requirement Scenario「污染库值降级零请求」）：污染 symbol 记录渲染
 * KlineReview → 降级文案逐字出现+getOhlcvBySymbol/getChartIndicators
 * 零调用；P4 对照例（同 hypothesis_id 合法 symbol）→ 两请求有调用。
 * 归因依据：ParamsEcho eligible 与 useFullRangeKline/useOverlayIndicators
 * enabled 各含与闸门无关的条件（hypothesis_id 非空非 adhoc/bounds 非空/
 * hasAny）——两例同 detail/同 strategy（DNA_FIX 保 hasAny=true），唯一
 * 差异即 symbol 经闸门前后，零调用才可归因于闸门。
 * mock 形制沿用 RecordBrowser.test.tsx（服务层全 mock 零外网；
 * KlineChart mock——jsdom 不渲染真图）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/services/validationRecords", async () => {
  const actual = await vi.importActual("@/services/validationRecords");
  return {
    ...actual,
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

// KlineChart mock：jsdom 不渲染真图——onChartReady 同步触发（真组件在
// chart 就绪时调），attachSegmentBand 层 mock 防 chart=null 崩
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
    onChartReady?: (chart: unknown, series: unknown) => void;
    ref?: { current: unknown };
  }) => {
    if (props.ref) props.ref.current = { scrollToTime: vi.fn(), setObservationSeries: vi.fn() };
    props.onChartReady?.(null, { fake: "series" });
    return <div data-testid="vr-kline" />;
  },
}));

import { RecordDetail } from "@/components/validationRecords/RecordDetail";
import {
  getValidationRecordDetail,
  getUnionBalance,
  type ValidationDetailResponse,
} from "@/services/validationRecords";
import { getChartIndicators, getOhlcvBySymbol } from "@/services/datasets";
import { getStrategy } from "@/services/strategies";
import type { ValidationRecordRow } from "@/services/workbench";

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
    statement: null,
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
        trades: [],
        equity: { grid_aligned: true, timeframe: "30m", start: "2026-04-01 00:00:00+00:00", length: 4, timestamps: null, values: [100000, 101000, 100500, 112400] },
        trail_series: [],
        trace_rows: [],
      },
    ],
    ...over,
  };
}

function makeStrategy(dna: unknown) {
  return { dna, strategy_id: "st_y1", name: "s", generation: 1 } as never;
}

/** DNA 带 EMA 基因——两例同配，保 useOverlayIndicators hasAny=true：
 *  污染例零调用不可能由 overlays 兜底造成 */
const DNA_EMA = { signal_genes: [{ indicator: "EMA", params: { period: 50 } }] };

function ui(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockDetail.mockResolvedValue(makeDetail());
  mockGetStrategy.mockResolvedValue(makeStrategy(DNA_EMA));
  mockUnion.mockResolvedValue({
    hypothesis_id: "hyp_x1", symbol: "BTCUSDT", timeframe: "30m",
    judged: [], remaining_bars: 0, remaining: [], remaining_time: "",
  } as never);
});

describe("RecordDetail 库值污染降级（T10 污染例+P4 对照例）", () => {
  it("T10 污染例：symbol 污染记录——降级文案逐字+K 线两请求零调用", async () => {
    ui(
      <RecordDetail
        record={makeRow({
          params: {
            request_range: { symbol: "../evil", timeframe: "30m", start: "2026-04-01", end: "2026-09-01" },
          },
        })}
      />,
    );
    // 降级文案逐字（KlineReview !symbol||!timeframe 分支——污染经值域闸
    // 置 null 走既有降级呈现，不新增 UI 状态）
    expect(
      await screen.findByText("行 params 无 symbol/timeframe——K 线区间不可定"),
    ).toBeTruthy();
    // K 线两请求零调用：detail/bounds/hasAny 均满足（对照组证明），
    // 唯一拦截即闸门置 null → useFullRangeKline/useOverlayIndicators
    // enabled false
    expect(mockOhlcv).not.toHaveBeenCalled();
    expect(mockChartInd).not.toHaveBeenCalled();
  });

  it("T10·P4 对照例：同 hypothesis_id 合法 symbol——两请求有调用（零调用归因闸门）", async () => {
    mockChartInd.mockResolvedValue({
      ema: { "50": [{ time: "2026-04-05 00:00:00+00:00", value: 1 }] },
      boll: null, rsi: null, macd: null, kdj: null,
    } as never);
    ui(<RecordDetail record={makeRow()} />);
    // 同一渲染路径下合法值确实发起调用——污染例的零调用才可归因于闸门
    // （而非 eligible/enabled 链的其他守卫兜底）
    await waitFor(() => {
      expect(mockOhlcv).toHaveBeenCalledWith(
        "BTCUSDT", "30m",
        expect.objectContaining({ start: "2026-04-01", end: "2026-09-01", limit: 50000 }),
      );
    });
    await waitFor(() => {
      expect(mockChartInd).toHaveBeenCalledWith("BTCUSDT", "30m", expect.objectContaining({
        ema_periods: "50",
      }));
    });
  });
});
