/**
 * 验证记录语义纯函数测（upgrade-validation-records-list 5.1/5.2）：
 * 卡片四段模型三型提取逐值（isolated/random/staged+composite 死值域+
 * result 空）+事件码动作/色族映射+标记形制+symbol/timeframe 三型路由。
 * 数据零观点——断言对照后端 result/params 实际形状（validation_service
 * 三型组装实锚）。
 */
import { describe, it, expect } from "vitest";
import {
  buildCardModel,
  eventSemantics,
  recordSymbolTimeframe,
  traceMarkers,
  TRACE_FAMILY_COLOR,
} from "@/components/validationRecords/traceSemantics";
import type { ValidationRecordRow } from "@/services/validationRecords";

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
    params: null,
    result: null,
    statement: null,
    ...over,
  };
}

describe("buildCardModel 三型提取（卡片四段对照逐值）", () => {
  it("isolated：metrics 直嵌+request_range 区间+result.symbol", () => {
    const row = makeRow({
      level: "isolated_backtest",
      statement: "当 RSI(14)<30 且量能>2倍均值时做多",
      params: {
        request_range: { symbol: "ETHUSDT", timeframe: "4h", start: "2026-08-01", end: "2026-09-30" },
      },
      result: {
        metrics: { total_return: 0.349, sharpe_ratio: 2.31, max_drawdown: -0.083, total_trades: 34 },
        symbol: "ETHUSDT",
        timeframe: "4h",
      },
    });
    const m = buildCardModel(row);
    expect(m.typeLabel).toBe("隔离验证");
    expect(m.statement).toBe("当 RSI(14)<30 且量能>2倍均值时做多");
    expect(m.rangeLabel).toBe("2026-08-01 → 2026-09-30");
    expect(m.symbol).toBe("ETHUSDT");
    expect(m.trades).toBe(34);
    expect(m.metrics).toEqual({
      total_return: 0.349, sharpe_ratio: 2.31, max_drawdown: -0.083,
    });
    expect(m.worstLabel).toBeNull();
  });

  it("random：worst_window 槽位+最差窗口标签+params 平铺 symbol/区间", () => {
    const row = makeRow({
      level: "random_window",
      params: { symbol: "BTCUSDT", timeframe: "1h", data_start: "2026-01-01", data_end: "2026-06-30", n_windows: 8 },
      result: {
        windows: [],
        summary: {
          worst_window: {
            start: "2026-03-01", end: "2026-03-15",
            metrics: { total_return: -0.121, sharpe_ratio: -0.8, max_drawdown: -0.15, total_trades: 5 },
          },
        },
      },
    });
    const m = buildCardModel(row);
    expect(m.typeLabel).toBe("随机窗口");
    expect(m.rangeLabel).toBe("2026-01-01 → 2026-06-30");
    expect(m.symbol).toBe("BTCUSDT");
    expect(m.trades).toBe(5);
    expect(m.metrics?.total_return).toBe(-0.121);
    expect(m.worstLabel).toBe("最差窗口");
  });

  it("staged：worst_segment 五键提取+最弱段标签+存量 sharpe 容缺 null", () => {
    const row = makeRow({
      level: "staged_validation",
      params: {
        request_range: { symbol: "ETHUSDT", timeframe: "4h", start: "2026-02-01", end: "2026-05-01" },
      },
      result: {
        segments: [],
        summary: {
          worst_segment: { label: "趋势", total_return: -0.05, sharpe_ratio: null, max_drawdown: null, total_trades: 12 },
        },
      },
    });
    const m = buildCardModel(row);
    expect(m.typeLabel).toBe("分段验证");
    expect(m.symbol).toBe("ETHUSDT");
    expect(m.trades).toBe(12);
    expect(m.metrics).toEqual({ total_return: -0.05, sharpe_ratio: null, max_drawdown: null });
    expect(m.worstLabel).toBe("最弱段·趋势");
  });

  it("composite 死值域与 result 空：无指标留痕（metrics=null）", () => {
    expect(buildCardModel(makeRow({ level: "composite" })).metrics).toBeNull();
    expect(buildCardModel(makeRow()).metrics).toBeNull();
    // isolated result 无 metrics 键同态
    expect(
      buildCardModel(makeRow({ result: { symbol: "BTCUSDT" } })).metrics,
    ).toBeNull();
  });

  it("staged 存量 result 内嵌 request_range 兜底 symbol（幂等补齐键）", () => {
    const row = makeRow({
      level: "staged_validation",
      params: {},
      result: { request_range: { symbol: "BTCUSDT", timeframe: "1d" } },
    });
    expect(recordSymbolTimeframe(row)).toEqual({ symbol: "BTCUSDT", timeframe: "1d" });
  });
});

describe("eventSemantics/traceMarkers（色点单源+四类动作）", () => {
  it("蓝族 10-13/金族 20-25/灰族不在标记（仅 filled）", () => {
    expect(eventSemantics(10)).toMatchObject({ action: "open", family: "strategy", markerText: "▲开多" });
    expect(eventSemantics(11)).toMatchObject({ action: "add", family: "strategy", markerText: "▲加多" });
    expect(eventSemantics(12)).toMatchObject({ action: "reduce", family: "strategy", markerText: "▽减仓" });
    expect(eventSemantics(13)).toMatchObject({ action: "exit", family: "strategy", markerText: "▼平多" });
    expect(eventSemantics(20)).toMatchObject({ action: "exit", family: "risk", markerText: "▼平多" });
    expect(eventSemantics(30)).toBeNull();

    const rows = [
      { exec_ref: "main", bar_time: "2026-08-01 00:00:00+00:00", seq: 0, outcome: "filled" as const, reason: null, event_code: 10, size: 1, price: 100, capped: null, gene_hits: null },
      { exec_ref: "main", bar_time: "2026-08-02 00:00:00+00:00", seq: 0, outcome: "filled" as const, reason: null, event_code: 20, size: 1, price: 90, capped: null, gene_hits: null },
      { exec_ref: "main", bar_time: "2026-08-03 00:00:00+00:00", seq: 0, outcome: "rejected" as const, reason: "exposure_cap", event_code: 30, size: null, price: null, capped: null, gene_hits: null },
    ];
    const markers = traceMarkers(rows);
    expect(markers).toHaveLength(2);
    expect(markers[0]).toMatchObject({ position: "belowBar", shape: "arrowUp", text: "▲开多" });
    expect(markers[0].color).toBe(TRACE_FAMILY_COLOR.strategy);
    expect(markers[1]).toMatchObject({ position: "aboveBar", shape: "arrowDown", color: TRACE_FAMILY_COLOR.risk });
  });
});
