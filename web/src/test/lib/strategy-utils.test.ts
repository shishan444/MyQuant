/**
 * Unit tests for lib/strategy-utils.ts: strategy type and name helpers.
 */
import { describe, it, expect } from "vitest";
import { getStrategyType, getStrategyName } from "@/lib/strategy-utils";
import type { DNA } from "@/types/api";

const makeDNA = (overrides: Partial<DNA> = {}): DNA => ({
  signal_genes: [
    {
      indicator: "RSI",
      params: { period: 14 },
      role: "entry_trigger",
      condition: { type: "lt", value: 30 },
    },
  ],
  logic_genes: { entry_logic: "AND", exit_logic: "AND" },
  execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
  risk_genes: {
    stop_loss: 0.05,
    take_profit: 0.1,
    position_size: 0.3,
    leverage: 1,
    direction: "long",
  },
  strategy_id: "test-id",
  generation: 0,
  parent_ids: [],
  mutation_ops: [],
  ...overrides,
});

describe("getStrategyType", () => {
  it("returns type for RSI strategy", () => {
    expect(getStrategyType(makeDNA())).toBe("动量");
  });

  it("returns type for EMA strategy", () => {
    const dna = makeDNA({
      signal_genes: [{
        indicator: "EMA",
        params: { period: 20 },
        role: "entry_trigger",
        condition: { type: "price_above" },
      }],
    });
    expect(getStrategyType(dna)).toBe("趋势");
  });

  it("returns unknown for null", () => {
    expect(getStrategyType(null)).toBe("未知");
  });

  it("returns unknown for undefined", () => {
    expect(getStrategyType(undefined)).toBe("未知");
  });

  it("returns mixed for unknown indicator", () => {
    const dna = makeDNA({
      signal_genes: [{
        indicator: "CUSTOM",
        params: {},
        role: "entry_trigger",
        condition: { type: "gt", value: 0 },
      }],
    });
    expect(getStrategyType(dna)).toBe("混合");
  });

  it("reads from layers when available", () => {
    const dna = makeDNA({
      layers: [{
        timeframe: "4h",
        signal_genes: [{
          indicator: "BB",
          params: { period: 20, std: 2 },
          role: "entry_trigger",
            condition: { type: "price_above" },
        }],
        logic_genes: { entry_logic: "AND", exit_logic: "AND" },
      }],
    });
    expect(getStrategyType(dna)).toBe("波动");
  });
});

describe("getStrategyName", () => {
  it("includes indicator type, direction, and timeframe", () => {
    const name = getStrategyName(makeDNA());
    expect(name).toContain("RSI");
    expect(name).toContain("做多");
    expect(name).toContain("4H");
  });

  it("short direction for short strategy", () => {
    const dna = makeDNA({
      risk_genes: {
        stop_loss: 0.05,
        take_profit: 0.1,
        position_size: 0.3,
        leverage: 1,
        direction: "short",
      },
    });
    expect(getStrategyName(dna)).toContain("做空");
  });

  it("returns default for null", () => {
    expect(getStrategyName(null)).toBe("未命名策略");
  });
});
