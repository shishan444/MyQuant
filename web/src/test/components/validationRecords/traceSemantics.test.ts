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
  legacySide,
  markerMorphText,
  markerShape,
  markerSide,
  normalizeDirection,
  recordSymbolTimeframe,
  resolveDirectionLookup,
  traceMarkers,
  TRACE_FAMILY_COLOR,
} from "@/components/validationRecords/traceSemantics";
import type { ValidationRecordRow, VrExecution, VrTrade, VrTraceRow } from "@/services/validationRecords";

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

/* ══ 库值 symbol/timeframe 值域闸（fix-ohlcv-path-segment-encoding 契约 1；
    validation-records-page「库值 symbol/timeframe 值域闸」Requirement）══ */

describe("recordSymbolTimeframe 值域闸（三型统一出口过闸）", () => {
  // T6：三型合法提取照旧——闸门加入后合法值逐字段原值返回（零误杀）
  it("T6 三型合法提取照旧：isolated result 直嵌 / random params 平铺 / staged request_range", () => {
    const iso = makeRow({
      level: "isolated_backtest",
      params: { request_range: { symbol: "BTCUSDT", timeframe: "4h", start: "2026-08-01", end: "2026-09-30" } },
      result: { symbol: "BTCUSDT", timeframe: "4h" },
    });
    expect(recordSymbolTimeframe(iso)).toEqual({ symbol: "BTCUSDT", timeframe: "4h" });

    const rnd = makeRow({
      level: "random_window",
      params: { symbol: "ETHUSDT", timeframe: "1h", data_start: "2026-01-01", data_end: "2026-06-30" },
    });
    expect(recordSymbolTimeframe(rnd)).toEqual({ symbol: "ETHUSDT", timeframe: "1h" });

    const stg = makeRow({
      level: "staged_validation",
      params: { request_range: { symbol: "XRPUSDT", timeframe: "1d", start: "2026-02-01", end: "2026-05-01" } },
    });
    expect(recordSymbolTimeframe(stg)).toEqual({ symbol: "XRPUSDT", timeframe: "1d" });
  });

  // T6·P2：合法形态全覆盖（字符类+长度闭区间双侧）——symbol/timeframe
  // 两字段同形传入，证明白名单对合法形态零误杀
  it.each([
    { value: "BTCUSDT", note: "纯字母数字" },
    { value: "BTC-USDT", note: "连字符" },
    { value: "BTC_USDT", note: "下划线" },
    { value: "1h", note: "小写周期" },
    { value: "1D", note: "混合大小写别名" },
    { value: "A".repeat(32), note: "恰好 32 字符（长度上界）" },
    { value: "x", note: "恰好 1 字符（长度下界）" },
  ])("T6·P2 合法形态原值放行：$note", ({ value }) => {
    const row = makeRow({
      level: "isolated_backtest",
      result: { symbol: value, timeframe: value },
    });
    expect(recordSymbolTimeframe(row)).toEqual({ symbol: value, timeframe: value });
  });

  // T7：isolated 型 result.symbol 污染——闸后 null、timeframe 独立保留
  it("T7 isolated 污染 symbol 闸为 null（timeframe 独立判定保留）", () => {
    const row = makeRow({
      level: "isolated_backtest",
      result: { symbol: "../evil", timeframe: "4h" },
    });
    expect(recordSymbolTimeframe(row)).toEqual({ symbol: null, timeframe: "4h" });
  });

  // T7a：其余型（staged）request_range.symbol 污染——第三型同闸直接证据
  //（T7/T9 只覆盖 isolated/random 两型，「三型统一出口过闸」需第三型在位）
  it("T7a staged request_range.symbol 污染同闸为 null", () => {
    const row = makeRow({
      level: "staged_validation",
      params: { request_range: { symbol: "../evil", timeframe: "4h", start: "2026-02-01", end: "2026-05-01" } },
    });
    expect(recordSymbolTimeframe(row)).toEqual({ symbol: null, timeframe: "4h" });
  });

  // T8：非法形态闸为 null——含点形态结构性排除（encodeURIComponent 对
  // '.' 恒等、'..' 可被 URL 规范化重组，见 SYMBOL_TIMEFRAME_RE 注释）
  it.each([
    { value: "A".repeat(33), note: "33 字符超长" },
    { value: "", note: "空串" },
    { value: "BTC.USDT", note: "含点" },
    { value: "..", note: "纯点段" },
    { value: "BTC\\USDT", note: "含反斜杠" },
  ])("T8 非法形态闸为 null：$note", ({ value }) => {
    const row = makeRow({
      level: "isolated_backtest",
      result: { symbol: value, timeframe: "4h" },
    });
    expect(recordSymbolTimeframe(row)).toEqual({ symbol: null, timeframe: "4h" });
  });

  // T9：random 型单侧污染独立判定——timeframe 污染闸 null、symbol 原值
  it("T9 random 单侧污染：timeframe 污染 null、symbol 原值保留", () => {
    const row = makeRow({
      level: "random_window",
      params: { symbol: "BTCUSDT", timeframe: "a#b", data_start: "2026-01-01", data_end: "2026-06-30" },
    });
    expect(recordSymbolTimeframe(row)).toEqual({ symbol: "BTCUSDT", timeframe: null });
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

  it("CARD-03 malformed bar 混入不串位：落点取自身所属 bar 的 timestamp（同域索引回填）", () => {
    // 首位混入：吸附域 [04-01,04-02,04-03]——旧版回填 bars[idx] 串到 bars[0]="bad-date-xx"
    const leadBad = [
      { timestamp: "bad-date-xx" },
      { timestamp: "2026-04-01T00:00:00Z" },
      { timestamp: "2026-04-02T00:00:00Z" },
      { timestamp: "2026-04-03T00:00:00Z" },
    ];
    const r1 = snapToBars(["2026-04-01T12:00:00Z"], leadBad, 86400);
    expect(r1.snapped).toEqual([
      { from: "2026-04-01T12:00:00Z", to: "2026-04-01T00:00:00Z" },
    ]);
    // 中位混入：点 04-02 18:00 归 barEpochs idx=1——旧版回填 bars[1]="bad-date-xx"（错一根）
    const midBad = [
      { timestamp: "2026-04-01T00:00:00Z" },
      { timestamp: "bad-date-xx" },
      { timestamp: "2026-04-02T00:00:00Z" },
      { timestamp: "2026-04-03T00:00:00Z" },
    ];
    const r2 = snapToBars(["2026-04-02T18:00:00Z"], midBad, 86400);
    expect(r2.snapped).toEqual([
      { from: "2026-04-02T18:00:00Z", to: "2026-04-02T00:00:00Z" },
    ]);
    // malformed bar 不构成可落点域+drop 语义未动（malformed 点/早于首 bar 丢弃计数）
    const r3 = snapToBars(
      ["garbage", "2026-03-31T00:00:00Z", "2026-04-01T06:00:00Z"],
      leadBad, 86400,
    );
    expect(r3.snapped).toEqual([
      { from: "2026-04-01T06:00:00Z", to: "2026-04-01T00:00:00Z" },
    ]);
    expect(r3.dropped).toBe(2);
  });
});


/* ══ fe-vr-marker-fidelity T1：方向归一（契约 0）/回退链（契约 1）/
    字义代入（契约 2）/侧位口径（契约 4）——既有断言组零改动 ══ */

describe("normalizeDirection 白名单严格归一（契约 0，v2.2 定谳口径）", () => {
  it("NF-01 白名单小写 long → long（记录级 result.direction 值域形）", () => {
    expect(normalizeDirection("long")).toBe("long");
  });
  it("NF-02 白名单首字母大写 Long → long（VrTrade.direction 值域形，vbt 序列化）", () => {
    expect(normalizeDirection("Long")).toBe("long");
  });
  it("NF-03 白名单小写 short → short", () => {
    expect(normalizeDirection("short")).toBe("short");
  });
  it("NF-04 白名单首字母大写 Short → short", () => {
    expect(normalizeDirection("Short")).toBe("short");
  });
  it("NF-05 记录级特态 mixed → null（双向策略记录级无法定单笔方向——触发契约 1 行级解析分支，永不成为行方向输出）", () => {
    expect(normalizeDirection("mixed")).toBeNull();
  });
  it("NF-06 undefined → null（2026-10-15 前存量记录无 direction 键）", () => {
    expect(normalizeDirection(undefined)).toBeNull();
  });
  it("NF-07 null → null（VrTrade.direction 可空形）", () => {
    expect(normalizeDirection(null)).toBeNull();
  });
  it("NF-08 脏值一律 null——含 \"LONG\" 全大写变体（非 toLowerCase 语义）、带尾空格、近形词、空串", () => {
    expect(normalizeDirection("LONG")).toBeNull();
    expect(normalizeDirection("Short ")).toBeNull();
    expect(normalizeDirection("longish")).toBeNull();
    expect(normalizeDirection("")).toBeNull();
  });
});

describe("resolveDirectionLookup 方向解析回退链（契约 1，四层分派）", () => {
  const trade = (over: Partial<VrTrade> = {}): VrTrade => ({
    seq: 0,
    entry_time: "2026-08-01 00:00:00+00:00",
    exit_time: "2026-08-02 00:00:00+00:00",
    entry_price: 100,
    exit_price: 110,
    size: 1,
    direction: "Long",
    pnl: 10,
    ...over,
  });
  const traceRow = (over: Partial<VrTraceRow> = {}): VrTraceRow => ({
    exec_ref: "main",
    bar_time: "2026-08-01 00:00:00+00:00",
    seq: 0,
    outcome: "filled",
    reason: null,
    event_code: 10,
    size: 1,
    price: 100,
    capped: null,
    gene_hits: null,
    ...over,
  });
  const execOf = (trades: VrTrade[]): VrExecution => ({
    exec_ref: "main",
    exec_index: 0,
    range_start: "2026-08-01 00:00:00+00:00",
    range_end: "2026-08-31 00:00:00+00:00",
    trades,
    equity: { grid_aligned: false, timeframe: "4h", timestamps: null, values: [] },
    trail_series: null,
    trace_rows: [],
  });

  it("DL-01 记录级 long → 份级常量（零 join：trades 空也直接定 long）", () => {
    const lookup = resolveDirectionLookup("long", execOf([]));
    expect(lookup(traceRow())).toBe("long");
    expect(lookup(traceRow({ event_code: 20 }))).toBe("long");
  });
  it("DL-02 记录级 short → 份级常量", () => {
    const lookup = resolveDirectionLookup("short", execOf([]));
    expect(lookup(traceRow())).toBe("short");
  });
  it("DL-03 mixed → 行级查表：入场行分钟键命中入场桶，归一笔方向", () => {
    const lookup = resolveDirectionLookup("mixed", execOf([trade()]));
    expect(lookup(traceRow())).toBe("long");
  });
  it("DL-04 undefined（存量无 direction 键）→ 行级查表回退逐笔", () => {
    const lookup = resolveDirectionLookup(undefined, execOf([trade({ direction: "Short" })]));
    expect(lookup(traceRow())).toBe("short");
  });
  it("DL-05 记录级脏值（\"LONG\"）→ 归一失败按缺失走行级查表", () => {
    const lookup = resolveDirectionLookup("LONG", execOf([trade({ direction: "Short" })]));
    expect(lookup(traceRow())).toBe("short");
  });
  it("DL-06 出场族行查出场桶（13/20-25 出场锚=exit_time；12 reduce 同查出场桶）", () => {
    const trades = [
      trade({ seq: 1, exit_time: "2026-08-05 00:00:00+00:00", direction: "Short" }),
      trade({ seq: 2, entry_time: "2026-08-06 00:00:00+00:00", exit_time: "2026-08-07 00:00:00+00:00", direction: "Long" }),
    ];
    const lookup = resolveDirectionLookup("mixed", execOf(trades));
    expect(lookup(traceRow({ event_code: 13, bar_time: "2026-08-05 00:00:00+00:00" }))).toBe("short");
    expect(lookup(traceRow({ event_code: 20, bar_time: "2026-08-05 00:00:00+00:00" }))).toBeNull(); // 桶耗尽（同桶一笔已被 13 行消费）
    expect(lookup(traceRow({ event_code: 12, bar_time: "2026-08-07 00:00:00+00:00" }))).toBe("long");
  });
  it("DL-07 桶序多笔分配：同桶两笔按 seq 升序逐行分配（复刻 _match consumed 语义）", () => {
    const trades = [
      trade({ seq: 1, entry_time: "2026-08-01 00:00:00+00:00", direction: "Long" }),
      trade({ seq: 2, entry_time: "2026-08-01 00:00:30+00:00", direction: "Short" }),
    ];
    const lookup = resolveDirectionLookup("mixed", execOf(trades));
    expect(lookup(traceRow({ bar_time: "2026-08-01 00:00:00+00:00" }))).toBe("long");
    expect(lookup(traceRow({ bar_time: "2026-08-01 00:00:45+00:00" }))).toBe("short");
  });
  it("DL-08 桶耗尽 → 该行 null（不循环消费不猜）", () => {
    const lookup = resolveDirectionLookup("mixed", execOf([trade()]));
    expect(lookup(traceRow())).toBe("long");
    expect(lookup(traceRow({ bar_time: "2026-08-01 00:30:00+00:00" }))).toBeNull();
  });
  it("DL-09 桶不中（行分钟键无对应桶）→ null；非 filled 行/词表外码 → null 不消费", () => {
    const lookup = resolveDirectionLookup("mixed", execOf([trade()]));
    expect(lookup(traceRow({ bar_time: "2026-08-09 00:00:00+00:00" }))).toBeNull();
    expect(lookup(traceRow({ outcome: "rejected", event_code: 30 }))).toBeNull();
    expect(lookup(traceRow({ event_code: 35 }))).toBeNull();
  });
  it("DL-10 桶命中但笔方向归一失败（\"LONG\" 脏值）→ 该行 null", () => {
    const lookup = resolveDirectionLookup("mixed", execOf([trade({ direction: "LONG" })]));
    expect(lookup(traceRow())).toBeNull();
  });
  it("DL-11 trades 空 + mixed → 行级无可配对 → null（双失维持旧字义链路）", () => {
    const lookup = resolveDirectionLookup("mixed", execOf([]));
    expect(lookup(traceRow())).toBeNull();
  });
  it("DL-12 trades 输入乱序：桶内按 seq 升序消费（先 seq 小的笔）", () => {
    const trades = [
      trade({ seq: 2, entry_time: "2026-08-01 00:00:20+00:00", direction: "Short" }),
      trade({ seq: 1, entry_time: "2026-08-01 00:00:00+00:00", direction: "Long" }),
    ];
    const lookup = resolveDirectionLookup("mixed", execOf(trades));
    expect(lookup(traceRow({ bar_time: "2026-08-01 00:00:00+00:00" }))).toBe("long");
    expect(lookup(traceRow({ bar_time: "2026-08-01 00:00:40+00:00" }))).toBe("short");
  });
  it("DL-13 工厂纯：同参两次调用产出独立闭包、各自桶游标从零", () => {
    const exec = execOf([trade({ direction: "Long" })]);
    const lookupA = resolveDirectionLookup("mixed", exec);
    const lookupB = resolveDirectionLookup("mixed", exec);
    expect(lookupA(traceRow())).toBe("long");
    // A 已消费桶内唯一笔，B 闭包游标独立仍从零——同笔可再次配对
    expect(lookupB(traceRow())).toBe("long");
    expect(lookupA(traceRow({ bar_time: "2026-08-01 01:00:00+00:00" }))).toBeNull();
  });
});

describe("markerMorphText/markerShape 六字义矩阵（契约 2）", () => {
  it("SEM-01 方向敏感码全积矩阵：8 码 × long/short × morph/arrow 共 32 断言（箭头基准=买卖语义：开多/平空 up、开空/平多 down）", () => {
    const matrix: Array<[number, string, string, string, string]> = [
      [10, "开多", "开空", "arrowUp", "arrowDown"],
      [13, "平多", "平空", "arrowDown", "arrowUp"],
      [20, "平多", "平空", "arrowDown", "arrowUp"],
      [21, "平多", "平空", "arrowDown", "arrowUp"],
      [22, "平多", "平空", "arrowDown", "arrowUp"],
      [23, "平多", "平空", "arrowDown", "arrowUp"],
      [24, "平多", "平空", "arrowDown", "arrowUp"],
      [25, "平多", "平空", "arrowDown", "arrowUp"],
    ];
    for (const [code, morphLong, morphShort, arrowLong, arrowShort] of matrix) {
      const entry = eventSemantics(code);
      expect(entry).not.toBeNull();
      expect(markerMorphText(entry!, "long")).toBe(morphLong);
      expect(markerMorphText(entry!, "short")).toBe(morphShort);
      expect(markerShape(entry!, "long")).toBe(arrowLong);
      expect(markerShape(entry!, "short")).toBe(arrowShort);
    }
  });
  it("SEM-02 非向类 11/12：两态同值（morph.long===morph.short）+双 circle（方向不代入）", () => {
    for (const code of [11, 12]) {
      const entry = eventSemantics(code)!;
      expect(entry.morph.long).toBe(entry.morph.short);
      expect(entry.morph.long).toBe(eventSemantics(code)!.label);
      expect(entry.arrowLong).toBe("circle");
      expect(entry.arrowShort).toBe("circle");
      expect(markerMorphText(entry, "short")).toBe(entry.morph.long);
      expect(markerShape(entry, "long")).toBe("circle");
    }
  });
  it("SEM-03 label/action/family 三字段冻结对照（D5——TradeTable 锚点列/SignalTrace 动作列依赖面零变化）", () => {
    const frozen: Array<[number, "open" | "add" | "reduce" | "exit", "strategy" | "risk", string]> = [
      [10, "open", "strategy", "入场"],
      [11, "add", "strategy", "加仓"],
      [12, "reduce", "strategy", "减仓"],
      [13, "exit", "strategy", "信号离场"],
      [20, "exit", "risk", "止损"],
      [21, "exit", "risk", "止盈"],
      [22, "exit", "risk", "清算强平"],
      [23, "exit", "risk", "金额防线"],
      [24, "exit", "risk", "固定时刻"],
      [25, "exit", "risk", "max_hold"],
    ];
    for (const [code, action, family, label] of frozen) {
      expect(eventSemantics(code)).toMatchObject({ action, family, label });
    }
  });
  it("SEM-04 direction=null 维持旧字义（D6）：字义→morph.long、箭头→arrowLong（=现状 shape 规则逐位一致）", () => {
    expect(markerMorphText(eventSemantics(10)!, null)).toBe("开多");
    expect(markerMorphText(eventSemantics(13)!, null)).toBe("平多");
    expect(markerMorphText(eventSemantics(20)!, null)).toBe("平多");
    expect(markerShape(eventSemantics(10)!, null)).toBe("arrowUp");
    expect(markerShape(eventSemantics(13)!, null)).toBe("arrowDown");
    expect(markerShape(eventSemantics(20)!, null)).toBe("arrowDown");
    expect(markerShape(eventSemantics(11)!, null)).toBe("circle");
    expect(markerShape(eventSemantics(12)!, null)).toBe("circle");
  });
  it("SEM-05 词表外码返回 null 锁定：30 灰族不在表；35/36 无词条维持现状（Q1 基线不收编）", () => {
    expect(eventSemantics(30)).toBeNull();
    expect(eventSemantics(35)).toBeNull();
    expect(eventSemantics(36)).toBeNull();
  });
  it("SEM-06 词表覆盖域=10-25 十四码全有词条，域外代表码（26/29/34）无词条", () => {
    for (const code of [10, 11, 12, 13, 20, 21, 22, 23, 24, 25]) {
      expect(eventSemantics(code)).not.toBeNull();
    }
    for (const code of [26, 29, 34]) {
      expect(eventSemantics(code)).toBeNull();
    }
  });
});

describe("traceMarkers 行为面（T1 时点锚——旧签名现状；终态 draft 断言 T3 随签名切换落位）", () => {
  const rows = [
    { exec_ref: "main", bar_time: "2026-08-01 00:00:00+00:00", seq: 0, outcome: "filled" as const, reason: null, event_code: 10, size: 1, price: 100, capped: null, gene_hits: null },
    { exec_ref: "main", bar_time: "2026-08-02 00:00:00+00:00", seq: 0, outcome: "filled" as const, reason: null, event_code: 20, size: 1, price: 90, capped: null, gene_hits: null },
    { exec_ref: "main", bar_time: "2026-08-03 00:00:00+00:00", seq: 0, outcome: "rejected" as const, reason: "exposure_cap", event_code: 30, size: null, price: null, capped: null, gene_hits: null },
  ];
  it("MK-01 滤除行为：仅 filled 且有词条的行产标记（rejected 行与 30 无词条行滤除）", () => {
    expect(traceMarkers(rows)).toHaveLength(2);
  });
  it("MK-02 保序：输出序与输入序一致（time 升序由组装层负责）", () => {
    const markers = traceMarkers(rows);
    expect(markers.map((m) => m.time)).toEqual([
      "2026-08-01 00:00:00+00:00",
      "2026-08-02 00:00:00+00:00",
    ]);
  });
  it("MK-03 颜色族与时间透传：color=TRACE_FAMILY_COLOR[family]、time=bar_time 原值", () => {
    const markers = traceMarkers(rows);
    expect(markers[0].color).toBe(TRACE_FAMILY_COLOR.strategy);
    expect(markers[1].color).toBe(TRACE_FAMILY_COLOR.risk);
    expect(markers[0].time).toBe("2026-08-01 00:00:00+00:00");
  });
});

describe("markerSide/legacySide 侧位口径（契约 4，判定公式单源）", () => {
  const bar = { high: 110, low: 90 };
  it("SIDE-01 价=中点（含等号）→ aboveBar", () => {
    expect(markerSide(100, bar)).toBe("aboveBar");
  });
  it("SIDE-02 价>high → aboveBar（bar 外上侧自然得出就近侧）", () => {
    expect(markerSide(110.5, bar)).toBe("aboveBar");
  });
  it("SIDE-03 价<low → belowBar（bar 外下侧）", () => {
    expect(markerSide(89.5, bar)).toBe("belowBar");
  });
  it("SIDE-04 bar 内上下半区：上半→上、下半→下", () => {
    expect(markerSide(101, bar)).toBe("aboveBar");
    expect(markerSide(99, bar)).toBe("belowBar");
  });
  it("SIDE-05 一字 bar（high=low）退化面：中点=该价，≥ 成立 → aboveBar", () => {
    expect(markerSide(100, { high: 100, low: 100 })).toBe("aboveBar");
    expect(markerSide(99.9, { high: 100, low: 100 })).toBe("belowBar");
  });
  it("SIDE-06 legacySide 旧惯例四动作族：入场族（open/add）下方、出场族（reduce/exit）上方", () => {
    expect(legacySide("open")).toBe("belowBar");
    expect(legacySide("add")).toBe("belowBar");
    expect(legacySide("reduce")).toBe("aboveBar");
    expect(legacySide("exit")).toBe("aboveBar");
  });
});
