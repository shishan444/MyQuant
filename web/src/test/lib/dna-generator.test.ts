/**
 * Unit tests for lib/dna-generator.ts: DNA generation from validation conditions.
 */
import { describe, it, expect } from "vitest";
import { generateDnaFromValidation } from "@/lib/dna-generator";
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
    // Only EMA should be included (empty subject filtered)
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
