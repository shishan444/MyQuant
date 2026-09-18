import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

// Mock motion/react to avoid animation issues in tests
vi.mock("motion/react", () => ({
  useReducedMotion: () => false,
  motion: {
    div: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) =>
      <div {...props}>{children}</div>,
    span: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) =>
      <span {...props}>{children}</span>,
  },
  AnimatePresence: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));

import { StrategyDetail } from "@/components/StrategyDetail";
import type { DNA } from "@/types/api";

function makeDna(overrides: Partial<DNA> = {}): DNA {
  const signalGene = {
    role: "entry_trigger" as const,
    indicator: "RSI",
    params: { period: 14 },
    condition: { type: "lt" as const, value: 30 },
  };
  return {
    strategy_id: "test-dna-001",
    signal_genes: [signalGene],
    signals: [signalGene],
    risk_genes: {
      stop_loss: 0.02,
      take_profit: 0.04,
      position_size: 1.0,
      leverage: 1,
      direction: "long" as const,
    },
    logic_genes: {
      entry_logic: "AND" as const,
      exit_logic: "OR" as const,
    },
    execution_genes: {
      timeframe: "4h",
      symbol: "BTCUSDT",
    },
    ...overrides,
  } as DNA;
}

describe("StrategyDetail", () => {
  it("renders DNA signals without champion props", () => {
    const dna = makeDna();
    render(<StrategyDetail dna={dna} />);
    expect(screen.getByText(/RSI/)).toBeInTheDocument();
  });

  it("renders fitness >= 1.0 in emerald color", () => {
    const dna = makeDna();
    render(
      <StrategyDetail
        dna={dna}
        champion_fitness={1.25}
        champion_qualified={true}
      />
    );
    expect(screen.getByText("1.25")).toBeInTheDocument();
    expect(screen.getByText("达标")).toBeInTheDocument();
  });

  it("renders fitness < 1.0 in amber color with unqualified badge", () => {
    const dna = makeDna();
    render(
      <StrategyDetail
        dna={dna}
        champion_fitness={0.65}
        champion_qualified={false}
      />
    );
    expect(screen.getByText("0.65")).toBeInTheDocument();
    expect(screen.getByText("未达标")).toBeInTheDocument();
  });

  it("renders per-dimension satisfaction details", () => {
    const dna = makeDna();
    const satisfaction = {
      annual_return: { actual: 0.25, required: 0.15, ratio: 1.667, met: true },
      max_drawdown: { actual: 0.20, required: 0.30, ratio: 1.5, met: true },
      win_rate: { actual: 0.35, required: 0.40, ratio: 0.875, met: false },
    };

    render(
      <StrategyDetail
        dna={dna}
        champion_fitness={0.87}
        champion_satisfaction={satisfaction}
      />
    );

    // Section title
    expect(screen.getByText("达标详情")).toBeInTheDocument();

    // Dimension names
    expect(screen.getByText("annual_return")).toBeInTheDocument();
    expect(screen.getByText("max_drawdown")).toBeInTheDocument();
    expect(screen.getByText("win_rate")).toBeInTheDocument();

    // Check actual values rendered
    expect(screen.getByText("0.250")).toBeInTheDocument();
  });

  it("renders champion metrics section", () => {
    const dna = makeDna();
    render(
      <StrategyDetail
        dna={dna}
        champion_metrics={{
          annual_return: 0.30,
          sharpe_ratio: 1.5,
          max_drawdown: -0.15,
          win_rate: 0.55,
          calmar_ratio: 2.0,
          total_trades: 42,
        }}
      />
    );
    expect(screen.getByText("回测指标")).toBeInTheDocument();
    expect(screen.getByText("30.0%")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("does not render fitness section when props are undefined", () => {
    const dna = makeDna();
    render(<StrategyDetail dna={dna} />);
    expect(screen.queryByText("达标")).not.toBeInTheDocument();
    expect(screen.queryByText("未达标")).not.toBeInTheDocument();
    expect(screen.queryByText("达标详情")).not.toBeInTheDocument();
  });
});
