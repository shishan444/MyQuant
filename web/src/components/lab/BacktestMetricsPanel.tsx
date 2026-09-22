import { cn, formatNumber } from "@/lib/utils";
import type { BacktestResult } from "@/types/api";

interface BacktestMetricsPanelProps {
  result: BacktestResult;
}

export function BacktestMetricsPanel({ result }: BacktestMetricsPanelProps) {
  const items = [
    {
      label: "总收益",
      value: `${(result.total_return * 100).toFixed(2)}%`,
      color: result.total_return > 0 ? "text-emerald-400" : "text-red-400",
    },
    {
      label: "夏普比率",
      value: formatNumber(result.sharpe_ratio),
      color: result.sharpe_ratio > 1 ? "text-emerald-400" : result.sharpe_ratio > 0 ? "text-amber-400" : "text-red-400",
    },
    {
      label: "最大回撤",
      value: `${(result.max_drawdown * 100).toFixed(2)}%`,
      color: Math.abs(result.max_drawdown) < 0.1 ? "text-emerald-400" : Math.abs(result.max_drawdown) < 0.3 ? "text-amber-400" : "text-red-400",
    },
    {
      label: "胜率",
      value: `${(result.win_rate * 100).toFixed(1)}%`,
      color: result.win_rate > 0.5 ? "text-emerald-400" : result.win_rate > 0.3 ? "text-amber-400" : "text-red-400",
    },
    {
      label: "交易次数",
      value: String(result.total_trades),
      color: result.total_trades > 10 ? "text-slate-200" : "text-amber-400",
    },
    // 基准对照（improve-backtest-benchmark-disclosure 4.2）：Optional 字段
    // 值非 null/undefined 才入 items（旧后端形制无新键 → 三卡均不渲染）。
    // 基准=零费买入持有（中性色）；超额/年化超额正绿负红对齐总收益形制。
    ...(result.benchmark_return != null
      ? [
          {
            label: "基准收益",
            value: `${(result.benchmark_return * 100).toFixed(2)}%`,
            color: "text-slate-200",
          },
        ]
      : []),
    ...(result.excess_return != null
      ? [
          {
            label: "超额收益",
            value: `${(result.excess_return * 100).toFixed(2)}%`,
            color: result.excess_return > 0 ? "text-emerald-400" : "text-red-400",
          },
        ]
      : []),
    ...(result.alpha != null
      ? [
          {
            label: "年化超额",
            value: `${(result.alpha * 100).toFixed(2)}%`,
            color: result.alpha > 0 ? "text-emerald-400" : "text-red-400",
          },
        ]
      : []),
  ];

  return (
    <div>
      <div className="grid grid-cols-3 gap-2">
        {items.map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-slate-700/30 bg-white/[0.02] p-3 text-center"
          >
            <div className="text-[11px] text-slate-500">{item.label}</div>
            <div className={cn("mt-1 text-sm font-mono", item.color)}>
              {item.value}
            </div>
          </div>
        ))}
      </div>
      {result.total_funding_cost > 0 && (
        <div className="mt-2 text-[11px] text-amber-500/80">
          资金费用: {formatNumber(result.total_funding_cost)}
        </div>
      )}
      {result.liquidated && (
        <div className="mt-1 text-[11px] text-red-400">
          策略触发爆仓，仓位已强制平仓
        </div>
      )}
    </div>
  );
}
