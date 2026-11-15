/**
 * Unit tests for lib/dna-generator.ts: DNA generation from validation conditions.
 */
import { describe, it, expect } from "vitest";
import {
  generateDnaFromValidation,
  isActionSupported,
  mapActionToCondType,
  parseThreshold,
  validateLabConditions,
} from "@/lib/dna-generator";
import type { ConditionInput } from "@/types/api";

describe("generateDnaFromValidation", () => {
  const when: ConditionInput[] = [
    { logic: "AND", subject: "RSI", action: "lt", target: "30" },
  ];
  const then: ConditionInput[] = [
    { logic: "AND", subject: "RSI", action: "gt", target: "70" },
  ];

  it("produces a valid DNA object", () => {
    const dna = generateDnaFromValidation(when, then, "BTCUSDT", "4h");
    expect(dna).toHaveProperty("signal_genes");
    expect(dna).toHaveProperty("logic_genes");
    expect(dna).toHaveProperty("execution_genes");
    expect(dna).toHaveProperty("risk_genes");
    expect(dna).toHaveProperty("strategy_id");
  });

  it("maps WHEN to entry_trigger and THEN to exit_trigger", () => {
    const dna = generateDnaFromValidation(when, then, "BTCUSDT", "4h");
    const entry = dna.signal_genes.find((g) => g.role === "entry_trigger");
    const exit = dna.signal_genes.find((g) => g.role === "exit_trigger");
    expect(entry).toBeDefined();
    expect(exit).toBeDefined();
    expect(entry!.indicator).toBe("RSI");
  });

  it("sets execution genes from params", () => {
    const dna = generateDnaFromValidation(when, then, "ETHUSDT", "1h");
    expect(dna.execution_genes.symbol).toBe("ETHUSDT");
    expect(dna.execution_genes.timeframe).toBe("1h");
  });

  it("uses default risk genes", () => {
    const dna = generateDnaFromValidation(when, then, "BTCUSDT", "4h");
    expect(dna.risk_genes.stop_loss).toBe(0.05);
    expect(dna.risk_genes.leverage).toBe(1);
    expect(dna.risk_genes.direction).toBe("long");
  });

  it("generates unique strategy_id", () => {
    const dna1 = generateDnaFromValidation(when, then, "BTCUSDT", "4h");
    const dna2 = generateDnaFromValidation(when, then, "BTCUSDT", "4h");
    expect(dna1.strategy_id).not.toBe(dna2.strategy_id);
  });

  it("filters out empty subjects", () => {
    const whenEmpty: ConditionInput[] = [
      { logic: "AND", subject: "", action: "lt", target: "30" },
      { logic: "AND", subject: "EMA", action: "cross_above", target: "20" },
    ];
    const dna = generateDnaFromValidation(whenEmpty, [], "BTCUSDT", "4h");
    // 仅 EMA 应入选（空 subject 被过滤）
    expect(dna.signal_genes).toHaveLength(1);
    expect(dna.signal_genes[0].indicator).toBe("EMA");
  });

  it("maps touch action to price_above", () => {
    const whenTouch: ConditionInput[] = [
      { logic: "AND", subject: "EMA", action: "touch", target: "0" },
    ];
    const dna = generateDnaFromValidation(whenTouch, [], "BTCUSDT", "4h");
    expect(dna.signal_genes[0].condition.type).toBe("price_above");
  });

  it("extracts default params for RSI", () => {
    const dna = generateDnaFromValidation(when, then, "BTCUSDT", "4h");
    const rsiGene = dna.signal_genes.find((g) => g.indicator === "RSI");
    expect(rsiGene!.params).toEqual({ period: 14 });
  });
});

describe("CONDITION_TYPE_MAP 对拍锚（fix-audit-correctness F-01）", () => {
  // 后端 executor EVALUABLE_CONDITION_TYPES 17 合法集快照（硬编码——
  // 与 tests/strategy/_LEGAL_CASES 同源，漂移时两侧对拍红）
  const LEGAL_TYPES = new Set([
    "eq", "lt", "gt", "le", "ge",
    "cross_above", "cross_below",
    "price_above", "price_below",
    "cross_above_series", "cross_below_series",
    "lookback_any", "lookback_all", "within_bars_after",
    "touch_bounce", "role_reversal", "wick_touch",
  ]);

  it("每个映射目标都 ∈ 后端 17 合法集快照", () => {
    for (const action of [
      "gt", "lt", "ge", "le", "eq", "touch", "cross_above",
      "cross_below", "spike", "shrink", "breakout", "breakdown",
      "rise", "drop",
    ]) {
      const mapped = mapActionToCondType(action);
      expect(mapped, `动作 ${action} 的映射必须非空`).not.toBeNull();
      expect(
        LEGAL_TYPES.has(mapped!),
        `动作 ${action} 映射到 ${mapped}——不在后端合法集`,
      ).toBe(true);
    }
  });

  it("无映射动作不进映射表（禁存由 validateLabConditions 提示）", () => {
    for (const action of [
      "consecutive_up", "consecutive_down",
      "divergence_top", "divergence_bottom",
    ]) {
      expect(isActionSupported(action)).toBe(false);
      expect(mapActionToCondType(action)).toBeNull();
    }
  });

  it("无映射动作的 DNA 生成不产该基因（不静默兜底 gt）", () => {
    const whenBad: ConditionInput[] = [
      { logic: "AND", subject: "close", action: "consecutive_up", target: "3" },
      { logic: "AND", subject: "RSI", action: "lt", target: "30" },
    ];
    const dna = generateDnaFromValidation(whenBad, [], "BTCUSDT", "4h");
    expect(dna.signal_genes).toHaveLength(1);
    expect(dna.signal_genes[0].condition.type).toBe("lt");
  });
});

describe("validateLabConditions 可存性预检", () => {
  it("合法条件组零原因", () => {
    const problems = validateLabConditions(
      [{ action: "lt", target: "30" }],
      [{ action: "gt", target: "70" }],
    );
    expect(problems).toEqual([]);
  });

  it("无映射动作产禁存原因", () => {
    const problems = validateLabConditions(
      [{ action: "consecutive_up", target: "3" }],
      [],
    );
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("consecutive_up");
  });

  it("阈值不可数值化产原因（字符串阈值不再直传落库）", () => {
    const problems = validateLabConditions(
      [{ action: "lt", target: "abc" }],
      [],
    );
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("阈值");
  });

  it("parseThreshold 数值化：数字直通、数字串解析、垃圾串 null", () => {
    expect(parseThreshold(2.5)).toBe(2.5);
    expect(parseThreshold("70")).toBe(70);
    expect(parseThreshold("abc")).toBeNull();
    expect(parseThreshold(null)).toBeNull();
  });
});
