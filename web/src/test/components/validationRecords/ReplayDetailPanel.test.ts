/** 回放详情面板纯函数测试（fe-replay-presentation 任务 2.1/3.1/4.1）。 */
import { describe, expect, it } from "vitest";
import { eventSemantics, TRACE_FAMILY_COLOR } from "@/components/validationRecords/traceSemantics";
import {
  derivePositionFlow,
  geneDescription,
  replayMarker,
} from "@/components/validationRecords/ReplayDetailPanel";

const fill = (over: Partial<Parameters<typeof replayMarker>[0]> = {}) => ({
  bar_time: "2026-01-01T00:00:00Z", seq: 1, kind: "open",
  side: "long", price: 100, quantity: 1, fee: 0, realized_pnl: null,
  reason: null, ...over,
});

describe("六态标记映射（replayMarker）", () => {
  it("open long/short → 开多/开空（箭头方向随向）", () => {
    expect(replayMarker(fill({ kind: "open", side: "long" }))!.text).toBe("开多");
    expect(replayMarker(fill({ kind: "open", side: "short" }))!.text).toBe("开空");
  });
  it("add → 加仓带方向；reduce → 减仓带方向", () => {
    expect(replayMarker(fill({ kind: "add", side: "long" }))!.text).toBe("加仓");
    expect(replayMarker(fill({ kind: "reduce", side: "long" }))!.text).toBe("减仓");
  });
  it("close → 平多/平空；reduce_terminated 归平仓态（归类正确）", () => {
    const m = replayMarker(fill({
      kind: "close", side: "long", reason: "reduce_terminated" }))!;
    expect(m.text).toBe("平多");
    expect(m.action).toBe("exit");
  });
  it("exit_sl → 出场族平仓态", () => {
    expect(replayMarker(fill({
      kind: "exit_sl", side: "long" }))!.text).toBe("平多");
  });

  it("值域外 kind → null 防线（沿 vr 无词条滤除——marker 滤除"
      + " 流水显原文）", () => {
    expect(replayMarker(fill({ kind: "unknown_kind" }))).toBeNull();
    expect(replayMarker(fill({ kind: "" }))).toBeNull();
  });
  it("回放与 vr 同 kind 同字义同色（P3-CARD-05 单源一致性）："
      + "replayMarker 输出与 eventSemantics 同码词条逐键一致", () => {
    for (const [kind, code] of [
      ["open", 10], ["add", 11], ["reduce", 12], ["close", 13],
      ["exit_sl", 20],
    ] as const) {
      const m = replayMarker(fill({ kind }))!;
      const sem = eventSemantics(code)!;
      expect(m.text).toBe(sem.morph.long);
      expect(m.color).toBe(TRACE_FAMILY_COLOR[sem.family]);
      expect(m.action).toBe(sem.action);
    }
  });
});

describe("仓位流水派生（derivePositionFlow）", () => {
  it("开-加-减-平序列：前/量/后仓位逐笔正确", () => {
    const fills = [
      fill({ kind: "open", side: "long", quantity: 10 }),
      fill({ kind: "add", side: "long", quantity: 5 }),
      fill({ kind: "reduce", side: "long", quantity: 2 }),
      fill({ kind: "close", side: "long" }),
    ];
    const flow = derivePositionFlow(fills);
    // open：0→10；add：10→15；reduce：15→13（-2）；close：13→0（-13）
    expect(flow.map((r) => [r.before, r.qty, r.after])).toEqual([
      [0, 10, 10], [10, 5, 15], [15, -2, 13], [13, -13, 0],
    ]);
  });
  it("reduce_terminated 行触发原因=减仓终止", () => {
    const flow = derivePositionFlow([
      fill({ kind: "open", side: "long", quantity: 4 }),
      fill({ kind: "close", side: "long", reason: "reduce_terminated" }),
    ]);
    // 派生层保留原始 reason 码（审计值）；「减仓终止」中文映射在
    // 组件呈现层（td 渲染分支）——两层语义分界
    expect(flow[1].reason).toBe("reduce_terminated");
  });
});

describe("基因触发描述（geneDescription）", () => {
  it("join 命中：indicator+field+condition+target 简述式", () => {
    const sigs = [{
      // P3-CARD-02 键形制：signal.bar_time=决策锚（前一根）、
      // exec_bar_time=执行 bar（与 fill.bar_time 同轴——join 键）
      bar_time: "2025-12-31T20:00:00Z",
      exec_bar_time: "2026-01-01T00:00:00Z",
      kind: "open", outcome: "filled",
      fill_seq: 3, reject_reason: null,
      gene_hits: [{ indicator: "RSI", field: "rsi",
                    condition: "lt", target: 45 }],
    }];
    expect(geneDescription(sigs, "2026-01-01T00:00:00Z", 3))
      .toBe("RSI.rsi lt 45");
  });
  it("差一根 pending 族命中（CARD-02 验收）：join 键=exec_bar_time"
      + " 非 bar_time（决策锚前一根）——旧键形制下恒 miss 根治", () => {
    const sigs = [{
      bar_time: "2025-12-31T20:00:00Z",
      exec_bar_time: "2026-01-01T00:00:00Z",
      kind: "open", outcome: "filled",
      fill_seq: 1, reject_reason: null,
      gene_hits: [{ indicator: "EMA", condition: "crossover",
                    target: "ema_20" }],
    }];
    // join 目标=执行 bar（与 fill 同轴）→命中
    expect(geneDescription(sigs, "2026-01-01T00:00:00Z", 1))
      .toBe("EMA crossover ema_20");
    // 旧键倒退锚：若实现回退用 bar_time join，以 bar_time 调用
    // 才会命中——exec 键实现下此处必 miss
    expect(geneDescription(sigs, "2025-12-31T20:00:00Z", 1)).toBe("");
  });
  it("同根即时族回归（CARD-02 验收）：exit_sl 决策=执行同根"
      + "（exec_bar_time==bar_time）join 命中", () => {
    const sigs = [{
      bar_time: "2026-01-02T00:00:00Z",
      exec_bar_time: "2026-01-02T00:00:00Z",
      kind: "exit_sl", outcome: "filled",
      fill_seq: 5, reject_reason: null,
      gene_hits: [{ indicator: "ATR", condition: "gt", target: 2 }],
    }];
    expect(geneDescription(sigs, "2026-01-02T00:00:00Z", 5))
      .toBe("ATR gt 2");
  });
  it("未命中（无 gene_hits）→ 空串（如实话术由呈现层承载）", () => {
    const sigs = [{
      bar_time: "2025-12-31T20:00:00Z",
      exec_bar_time: "2026-01-01T00:00:00Z",
      kind: "open", outcome: "rejected",
      fill_seq: null, reject_reason: "below_min_notional",
      gene_hits: null,
    }];
    expect(geneDescription(sigs, "2026-01-01T00:00:00Z", 1)).toBe("");
  });
});
