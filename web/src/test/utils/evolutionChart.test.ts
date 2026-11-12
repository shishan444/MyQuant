/**
 * Tests for utils/evolutionChart.ts: parseDiagnostics + transformChartData.
 */
import { describe, it, expect } from "vitest";
import {
  parseDiagnostics,
  transformChartData,
} from "@/utils/evolutionChart";
import type { EvolutionHistoryRecord } from "@/types/api";

// ---------------------------------------------------------------------------
// parseDiagnostics
// ---------------------------------------------------------------------------

describe("parseDiagnostics", () => {
  it("returns empty for undefined input", () => {
    expect(parseDiagnostics(undefined)).toEqual({});
  });

  it("returns empty for empty string", () => {
    expect(parseDiagnostics("")).toEqual({});
  });

  it("parses population from pop= field", () => {
    const result = parseDiagnostics("best=85.2|pop=3");
    expect(result.population).toBe(3);
  });

  it("parses diag JSON with numeric diversity", () => {
    const result = parseDiagnostics('best=90|pop=2|diag={"avg_trades":12,"diversity":0.75}');
    expect(result.avgTrades).toBe(12);
    expect(result.diversity).toBe(0.75);
  });

  it("parses diag JSON with object diversity", () => {
    const result = parseDiagnostics('pop=5|diag={"diversity":{"genotype":0.55}}');
    expect(result.diversity).toBe(0.55);
  });

  it("returns only population when no diag field", () => {
    const result = parseDiagnostics("best=50|pop=10");
    expect(result.population).toBe(10);
    expect(result.avgTrades).toBeUndefined();
  });

  it("returns empty on malformed JSON in diag", () => {
    const result = parseDiagnostics("diag={invalid}");
    expect(result).toEqual({});
  });

  it("handles NaN population gracefully", () => {
    const result = parseDiagnostics("pop=abc");
    expect(result.population).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// transformChartData
// ---------------------------------------------------------------------------

function makeRecord(overrides: Partial<EvolutionHistoryRecord> = {}): EvolutionHistoryRecord {
  return {
    generation: 1,
    best_score: 50,
    avg_score: 40,
    best_fitness: 0.45,
    avg_fitness: 0.35,
    top3_summary: "",
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("transformChartData", () => {
  it("returns empty result for empty input", () => {
    const result = transformChartData([]);
    expect(result.data).toEqual([]);
    expect(result.championChanges).toEqual([]);
    expect(result.boundaries).toEqual([]);
    expect(result.stats.maxStagnation).toBe(0);
  });

  it("maps generation and scores", () => {
    const records = [
      makeRecord({ generation: 1, best_score: 50, avg_score: 40 }),
      makeRecord({ generation: 2, best_score: 55, avg_score: 45 }),
    ];
    const result = transformChartData(records);
    expect(result.data).toHaveLength(2);
    expect(result.data[0].generation).toBe(1);
    expect(result.data[0].bestScore).toBe(50);
    expect(result.data[1].bestScore).toBe(55);
  });

  it("computes cumulative best (never decreases)", () => {
    const records = [
      makeRecord({ generation: 1, best_score: 60 }),
      makeRecord({ generation: 2, best_score: 55 }),
      makeRecord({ generation: 3, best_score: 70 }),
    ];
    const result = transformChartData(records);
    expect(result.data[0].cumulativeBest).toBe(60);
    expect(result.data[1].cumulativeBest).toBe(60); // not 55
    expect(result.data[2].cumulativeBest).toBe(70);
  });

  it("detects champion changes", () => {
    const records = [
      makeRecord({ generation: 1, best_score: 50 }),
      makeRecord({ generation: 2, best_score: 55 }),
      makeRecord({ generation: 3, best_score: 55 }),
    ];
    const result = transformChartData(records);
    expect(result.stats.totalChampionChanges).toBe(2); // gen 1 and gen 2
    expect(result.data[0].isChampionChange).toBe(true);
    expect(result.data[1].isChampionChange).toBe(true);
    expect(result.data[2].isChampionChange).toBe(false);
  });

  it("detects population boundaries", () => {
    const records = [
      makeRecord({ generation: 1, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, top3_summary: "pop=1" }),
      makeRecord({ generation: 3, top3_summary: "pop=2" }),
    ];
    const result = transformChartData(records);
    expect(result.boundaries).toEqual([3]);
    expect(result.data[2].isPopulationBoundary).toBe(true);
  });

  it("counts stagnation correctly", () => {
    const records = [
      makeRecord({ generation: 1, best_score: 50 }),
      makeRecord({ generation: 2, best_score: 50 }),
      makeRecord({ generation: 3, best_score: 50 }),
      makeRecord({ generation: 4, best_score: 60 }),
    ];
    const result = transformChartData(records);
    expect(result.data[0].stagnationCount).toBe(0);
    expect(result.data[1].stagnationCount).toBe(1);
    expect(result.data[2].stagnationCount).toBe(2);
    expect(result.data[3].stagnationCount).toBe(0);
    expect(result.stats.maxStagnation).toBe(2);
  });

  it("resets stagnation at population boundary", () => {
    const records = [
      makeRecord({ generation: 1, best_score: 50, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, best_score: 50, top3_summary: "pop=1" }),
      makeRecord({ generation: 3, best_score: 50, top3_summary: "pop=2" }),
    ];
    const result = transformChartData(records);
    // gen 3 是种群边界，停滞计数清零
    expect(result.data[2].stagnationCount).toBe(0);
  });

  it("counts distinct populations", () => {
    const records = [
      makeRecord({ generation: 1, top3_summary: "pop=1" }),
      makeRecord({ generation: 2, top3_summary: "pop=2" }),
      makeRecord({ generation: 3, top3_summary: "pop=1" }),
    ];
    const result = transformChartData(records);
    expect(result.stats.populationCount).toBe(2);
  });
});
