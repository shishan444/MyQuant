/**
 * EvolvedStrategySources·DnaSummary 诚实呈现锚（CARD-06 truthfulness）。
 * 方向显式四分支（无兜底文案）/止损条件展示（无虚构值）/sl_mode 四档
 * 档位段/series 档止损「非声明值」标注。组件纯 props 直测（零服务 mock）。
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EvolvedStrategySources } from "@/components/evolution/EvolvedStrategySources";
import type { EvolvedStrategy } from "@/types/api";

function mkStrategy(riskOverrides: Record<string, unknown>): EvolvedStrategy {
  return {
    strategy_id: "st-1",
    source: "champion",
    score: 80,
    dna: {
      signal_genes: [],
      logic_genes: { entry_logic: "AND", exit_logic: "OR" },
      risk_genes: {
        direction: "long", leverage: 1, sl_mode: "pct",
        stop_loss: 0.05, take_profit: 0.1, position_size: 0.3,
        ...riskOverrides,
      },
      execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
      layers: null,
    } as unknown as EvolvedStrategy["dna"],
  };
}

function renderSummaryText(riskOverrides: Record<string, unknown>): string {
  const strategies = [mkStrategy(riskOverrides)];
  const { container } = render(
    <EvolvedStrategySources strategies={strategies} loading={false} error={null} />,
  );
  // DnaSummary 是 item 内唯一的 summary span（text-[11px] 文本行）
  return container.querySelector('[data-testid="evolved-source-item"] span.text-\\[11px\\]')?.textContent ?? "";
}

describe("CARD-06 DnaSummary 诚实呈现", () => {
  it("基线：long+pct 全字段 → 做多 · 百分比档 · 止损 5.0% · 止盈 10.0% · 0 个基因", () => {
    const text = renderSummaryText({});
    expect(text).toBe("做多 · 百分比档 · 止损 5.0% · 止盈 10.0% · 0 个基因");
  });

  it("series 档：档位标「序列档」+止损带「（非声明值）」标注（物化残留非声明值）", () => {
    const text = renderSummaryText({ sl_mode: "series" });
    expect(text).toContain("序列档");
    expect(text).toContain("止损 5.0%（非声明值）");
  });

  it("trail 档：档位标「移动档」（SL_MODE_LABELS 补 trail——旧表缺键回退英文原文）", () => {
    const text = renderSummaryText({ sl_mode: "trail" });
    expect(text).toContain("移动档");
    expect(text).not.toContain("trail");
  });

  it("direction 缺失：方向段省略，不兜底「做多」（旧实现 undefined 兜做多文案）", () => {
    const text = renderSummaryText({ direction: undefined });
    expect(text.startsWith("百分比档")).toBe(true);
    expect(text).not.toContain("做多");
  });

  it("stop_loss 缺失：止损段整体省略，不显示虚构 3.0%（旧实现 ?? 0.03 虚构值）", () => {
    const text = renderSummaryText({ stop_loss: null });
    expect(text).not.toContain("止损");
    expect(text).not.toContain("3.0%");
    expect(text).toContain("止盈 10.0%");
  });

  it("risk_genes 整体缺失：仅档位后段省略方向与止损，基因计数保留", () => {
    const strategies: EvolvedStrategy[] = [{
      strategy_id: "st-2",
      source: "snapshot",
      generation: 3,
      score: 70,
      dna: {
        signal_genes: [],
        logic_genes: { entry_logic: "AND", exit_logic: "OR" },
        execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
        layers: null,
      } as unknown as EvolvedStrategy["dna"],
    }];
    const { container } = render(
      <EvolvedStrategySources strategies={strategies} loading={false} error={null} />,
    );
    const text = container.querySelector('[data-testid="evolved-source-item"] span.text-\\[11px\\]')?.textContent ?? "";
    expect(text).toBe("0 个基因");
  });
});
