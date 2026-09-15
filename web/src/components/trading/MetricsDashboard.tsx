import { StatCard } from "@/components/StatCard";
import { formatCurrency, formatPercent } from "@/lib/utils";

interface MetricsData {
  total_return_pct: number;
  total_pnl: number;
  win_rate: number;
  profit_factor: number | null;
  max_drawdown_pct: number;
  avg_trade_pnl: number;
  total_trades: number;
  win_count: number;
  loss_count: number;
}

interface MetricsDashboardProps {
  metrics: MetricsData | undefined;
  initialCash: number;
}

function getTrend(value: number) {
  if (value > 0) return "up" as const;
  if (value < 0) return "down" as const;
  return "neutral" as const;
}

export function MetricsDashboard({ metrics }: MetricsDashboardProps) {
  if (!metrics) {
    return (
      <div className="grid grid-cols-3 gap-2 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <StatCard key={i} label="--" value="--" />
        ))}
      </div>
    );
  }

  const items = [
    {
      label: "总盈亏",
      value: formatCurrency(metrics.total_pnl),
      trend: getTrend(metrics.total_pnl),
    },
    {
      label: "收益率",
      value: formatPercent(metrics.total_return_pct / 100),
      trend: getTrend(metrics.total_return_pct),
    },
    {
      label: "胜率",
      value: `${(metrics.win_rate * 100).toFixed(1)}%`,
      trend: metrics.win_rate >= 0.5 ? ("up" as const) : ("down" as const),
    },
    {
      label: "盈亏比",
      value: metrics.profit_factor != null ? metrics.profit_factor.toFixed(2) : "N/A",
      trend: (metrics.profit_factor ?? 0) >= 1 ? ("up" as const) : ("down" as const),
    },
    {
      label: "最大回撤",
      value: `-${metrics.max_drawdown_pct.toFixed(1)}%`,
      trend: "down" as const,
    },
    {
      label: "交易次数",
      value: `${metrics.win_count}胜/${metrics.loss_count}负`,
      trend: "neutral" as const,
    },
  ];

  return (
    <div className="grid grid-cols-3 gap-2 lg:grid-cols-6">
      {items.map((item) => (
        <StatCard
          key={item.label}
          label={item.label}
          value={item.value}
          trend={item.trend}
        />
      ))}
    </div>
  );
}
