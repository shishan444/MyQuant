/**
 * Unit tests for lib/utils.ts: formatting and utility functions.
 */
import { describe, it, expect } from "vitest";
import {
  formatPercent,
  formatCurrency,
  formatNumber,
  formatPercentValue,
  formatDuration,
  formatTimestamp,
  cn,
  getFitnessColor,
} from "@/lib/utils";

describe("formatPercent", () => {
  it("positive value shows + sign", () => {
    expect(formatPercent(0.255)).toBe("+25.5%");
  });

  it("negative value shows - sign", () => {
    expect(formatPercent(-0.083)).toBe("-8.3%");
  });

  it("zero shows + sign", () => {
    expect(formatPercent(0)).toBe("+0.0%");
  });

  it("formats to 1 decimal place", () => {
    expect(formatPercent(0.12345)).toBe("+12.3%");
  });
});

describe("formatCurrency", () => {
  it("formats integer USD", () => {
    expect(formatCurrency(100000)).toBe("$100,000");
  });

  it("formats zero", () => {
    expect(formatCurrency(0)).toBe("$0");
  });
});

describe("formatNumber", () => {
  it("defaults to 2 decimals", () => {
    expect(formatNumber(3.14159)).toBe("3.14");
  });

  it("respects custom decimals", () => {
    expect(formatNumber(3.14159, 4)).toBe("3.1416");
  });
});

describe("formatPercentValue", () => {
  it("positive shows + sign", () => {
    expect(formatPercentValue(25.5)).toBe("+25.5%");
  });

  it("negative shows - sign", () => {
    expect(formatPercentValue(-8.3)).toBe("-8.3%");
  });
});

describe("formatDuration", () => {
  it("formats seconds", () => {
    const start = "2024-01-01T00:00:00Z";
    const end = "2024-01-01T00:00:30Z";
    expect(formatDuration(start, end)).toBe("30秒");
  });

  it("formats minutes", () => {
    const start = "2024-01-01T00:00:00Z";
    const end = "2024-01-01T00:05:00Z";
    expect(formatDuration(start, end)).toBe("5分钟");
  });

  it("formats hours", () => {
    const start = "2024-01-01T00:00:00Z";
    const end = "2024-01-01T03:00:00Z";
    expect(formatDuration(start, end)).toBe("3.0小时");
  });

  it("invalid input handles gracefully", () => {
    const result = formatDuration("invalid", "invalid");
    // 非法日期在 getTime() 产生 NaN，落入小时分支
    expect(typeof result).toBe("string");
  });
});

describe("formatTimestamp", () => {
  it("四档形制逐档锚（REQ-3d F1-F4）", () => {
    expect(formatTimestamp("2024-06-15T14:30:00+00:00", "minute")).toBe("2024-06-15 14:30");
    expect(formatTimestamp("2024-06-15T14:30:00+00:00", "second")).toBe("2024-06-15 14:30:00");
    expect(formatTimestamp("2024-06-15T14:30:00+00:00", "full")).toBe("2024-06-15 14:30:00+00:00");
    expect(formatTimestamp("2024-06-15T14:30:00+00:00", "date")).toBe("2024-06-15");
  });

  it("null/undefined 回退破折号", () => {
    expect(formatTimestamp(null, "minute")).toBe("\u2014");
    expect(formatTimestamp(undefined, "date")).toBe("\u2014");
  });
});

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    expect(cn("foo", false && "bar", "baz")).toBe("foo baz");
  });
});

describe("getFitnessColor", () => {
  it("returns green for sharpe >= 1.0", () => {
    expect(getFitnessColor(1.5)).toBe("text-emerald-400");
  });

  it("returns amber for sharpe between 0.5 and 1.0", () => {
    expect(getFitnessColor(0.75)).toBe("text-amber-400");
  });

  it("returns red for sharpe <= 0.5", () => {
    expect(getFitnessColor(0.3)).toBe("text-red-400");
  });

  it("uses calmar thresholds when objective=calmar", () => {
    expect(getFitnessColor(2.5, "calmar")).toBe("text-emerald-400");
    expect(getFitnessColor(1.5, "calmar")).toBe("text-amber-400");
    expect(getFitnessColor(0.5, "calmar")).toBe("text-red-400");
  });

  it("uses targetScore for annual_return objective", () => {
    expect(getFitnessColor(6.0, "annual_return", 6.0)).toBe("text-emerald-400");
    expect(getFitnessColor(3.0, "annual_return", 6.0)).toBe("text-amber-400");
    expect(getFitnessColor(1.0, "annual_return", 6.0)).toBe("text-red-400");
  });

  it("defaults to sharpe for unknown objective", () => {
    expect(getFitnessColor(1.5, "unknown")).toBe("text-emerald-400");
  });

  it("handles null/NaN gracefully", () => {
    expect(getFitnessColor(null as unknown as number)).toBe("text-slate-500");
    expect(getFitnessColor(NaN)).toBe("text-slate-500");
  });
});
