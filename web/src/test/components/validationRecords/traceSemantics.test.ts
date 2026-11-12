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
    expect(markers[0]).toMatchObject({ position: "belowBar", shape: "arrowUp", text: "▲开多 100.0" });
    expect(markers[0].color).toBe(TRACE_FAMILY_COLOR.strategy);
    expect(markers[1]).toMatchObject({ position: "aboveBar", shape: "arrowDown", color: TRACE_FAMILY_COLOR.risk, text: "▼平多 90.0" });
  });

  it("价格形制（upgrade-vr-fullrange-chart 契约 D）：有价=动作名+空格+一位小数；无价仅动作名不伪造", () => {
    const rows = [
      { exec_ref: "main", bar_time: "2026-08-01 00:00:00+00:00", seq: 0, outcome: "filled" as const, reason: null, event_code: 10, size: 1, price: 103209.28, capped: null, gene_hits: null },
      { exec_ref: "main", bar_time: "2026-08-02 00:00:00+00:00", seq: 0, outcome: "filled" as const, reason: null, event_code: 11, size: 1, price: null, capped: null, gene_hits: null },
    ];
    const [withPrice, withoutPrice] = traceMarkers(rows);
    expect(withPrice.text).toBe("▲开多 103209.3");
    expect(withoutPrice.text).toBe("▲加多");
  });
});

/* ══ 契约 H/K：DNA 指标提取+吸附（upgrade-vr-indicator-overlay）══ */
import {
  dnaIndicators,
  snapToBars,
  OVERLAY_UNSUPPORTED_TEXT,
  OVERLAY_SUBCHART_TEXT,
  OVERLAY_MTF_TEXT,
  LAYER_NOTE,
} from "@/components/validationRecords/traceSemantics";

describe("dnaIndicators 三分判别（契约 H）", () => {
  it("判定表：EMA/BB→overlayable（实测锚参数直映射）、RSI/MACD/KDJ→subChartOnly、其余→unsupported", () => {
    const out = dnaIndicators({
      signal_genes: [
        { indicator: "BB", params: { period: 20, std: 2 }, field: "middle" },
        { indicator: "EMA", params: { period: 50 } },
        { indicator: "RSI", params: { period: 14 } },
        { indicator: "ATR", params: { period: 14 } },
      ],
    });
    expect(out.overlayable).toEqual([
      { kind: "BB", period: 20, std: 2, field: "middle" },
      { kind: "EMA", period: 50 },
    ]);
    expect(out.subChartOnly.map((s) => s.kind)).toEqual(["RSI"]);
    expect(out.unsupported).toEqual([{ indicator: "ATR" }]);
  });

  it("多基因聚合：多 EMA periods 去重+多 BB 参数组去重+同参数重复去重", () => {
    const out = dnaIndicators({
      signal_genes: [
        { indicator: "EMA", params: { period: 50 } },
        { indicator: "EMA", params: { period: 7 } },
        { indicator: "EMA", params: { period: 50 } },
        { indicator: "BB", params: { period: 20, std: 2 } },
        { indicator: "BB", params: { period: 20, std: 2 } },
        { indicator: "BB", params: { period: 26, std: 2.5 } },
      ],
    });
    expect(out.overlayable).toEqual([
      { kind: "EMA", period: 50 },
      { kind: "EMA", period: 7 },
      { kind: "BB", period: 20, std: 2 },
      { kind: "BB", period: 26, std: 2.5 },
    ]);
  });

  it("layers 各层 signal_genes 全族→unsupported 跨周期层；缺失值三分全空不报错", () => {
    const out = dnaIndicators({
      signal_genes: [{ indicator: "EMA", params: { period: 50 } }],
      layers: [
        { timeframe: "1d", signal_genes: [{ indicator: "EMA", params: { period: 20 } }] },
        { timeframe: "1d", signal_genes: [{ indicator: "MACD" }] },
      ],
    });
    expect(out.unsupported).toEqual([
      { indicator: "EMA", note: OVERLAY_MTF_TEXT },
      { indicator: "MACD", note: OVERLAY_MTF_TEXT },
    ]);
    // 缺失值族
    expect(dnaIndicators(null)).toEqual({ overlayable: [], subChartOnly: [], unsupported: [] });
    expect(dnaIndicators({ signal_genes: [] })).toEqual({ overlayable: [], subChartOnly: [], unsupported: [] });
    expect(dnaIndicators({ signal_genes: [{ indicator: "EMA", params: {} }] }).unsupported)
      .toEqual([{ indicator: "EMA" }]);
  });

  it("降级文案常量（契约 L——可断言契约串）", () => {
    expect(OVERLAY_UNSUPPORTED_TEXT).toBe("该指标暂不支持叠加");
    expect(OVERLAY_SUBCHART_TEXT).toBe("副图已用于资金曲线，暂不叠加");
    expect(OVERLAY_MTF_TEXT).toBe("跨周期层指标暂不叠加");
    expect(LAYER_NOTE).toBe("跨周期层");
  });
});

describe("snapToBars 吸附（契约 K）", () => {
  const dayBars = [
    { timestamp: "2026-04-01T00:00:00Z" },
    { timestamp: "2026-04-02T00:00:00Z" },
    { timestamp: "2026-04-03T00:00:00Z" },
  ];
  it("左闭右开归属：t==bar.time 挂该 bar；t==下一根开盘挂下一根；段中点挂所在 bar", () => {
    const r = snapToBars(
      ["2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z", "2026-04-02T12:00:00Z"],
      dayBars,
      86400,
    );
    expect(r.snapped.map((p) => p.to)).toEqual([
      "2026-04-01T00:00:00Z",
      "2026-04-02T00:00:00Z",
      "2026-04-02T00:00:00Z",
    ]);
    expect(r.dropped).toBe(0);
  });
  it("超域点丢弃并计数（早于首 bar 与晚于尾 bar 覆盖域）", () => {
    const r = snapToBars(
      ["2026-03-31T00:00:00Z", "2026-04-02T06:00:00Z", "2026-04-04T00:00:00Z"],
      dayBars,
      86400,
    );
    expect(r.snapped.map((p) => p.to)).toEqual(["2026-04-02T00:00:00Z"]);
    expect(r.dropped).toBe(2);
  });
  it("空 bars/tf<=0 全丢；malformed 时间点丢弃", () => {
    expect(snapToBars(["x"], [], 86400)).toEqual({ snapped: [], dropped: 1 });
    expect(snapToBars(["2026-04-02T00:00:00Z"], dayBars, 0).dropped).toBe(1);
    expect(snapToBars(["garbage"], dayBars, 86400).dropped).toBe(1);
  });
});
