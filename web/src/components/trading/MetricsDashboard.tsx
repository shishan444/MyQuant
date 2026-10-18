import { StatCard } from "@/components/StatCard";
import { formatCurrency, formatPercent } from "@/lib/utils";
import type { TradingReport, TradingTask } from "@/types/api";

interface MetricsDashboardProps {
  /** 滚动报告（任意状态可取；undefined=加载中占位） */
  report: TradingReport | undefined;
  /** 任务行（总盈亏/胜负计数在任务行不在报告层） */
  task: TradingTask;
}

function getTrend(value: number) {
  if (value > 0) return "up" as const;
  if (value < 0) return "down" as const;
  return "neutral" as const;
}

/**
 * 验证报告第一层（绝对绩效层）主指标六卡：
 * compute_metrics 单源输出（与回测报告同族可横向比较）。
 */
export function MetricsDashboard({ report, task }: MetricsDashboardProps) {
  if (!report) {
    return (
      <div className="grid grid-cols-3 gap-2 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <StatCard key={i} label="--" value="--" />
        ))}
      </div>
    );
  }

  const perf = report.performance;
  const items = [
    {
      label: "总盈亏",
      value: formatCurrency(task.total_pnl),
      trend: getTrend(task.total_pnl),
    },
    {
      label: "年化收益",
      value: formatPercent(perf.annual_return),
      trend: getTrend(perf.annual_return),
    },
    {
      label: "胜率",
      // 零交易为缺失语义（报告层 win_rate=null），展示 "-" 不展示 0.0%
      value: perf.win_rate == null ? "-" : `${(perf.win_rate * 100).toFixed(1)}%`,
      trend:
        perf.win_rate != null && perf.win_rate >= 0.5
          ? ("up" as const)
          : ("down" as const),
    },
    {
      label: "盈亏比",
      value: perf.profit_factor != null ? perf.profit_factor.toFixed(2) : "N/A",
      trend: (perf.profit_factor ?? 0) >= 1 ? ("up" as const) : ("down" as const),
    },
    {
      label: "最大回撤",
      // max_drawdown 为负小数（drawdown.min()），×100 展示百分位
      value: `-${Math.abs(perf.max_drawdown * 100).toFixed(1)}%`,
      trend: "down" as const,
    },
    {
      label: "交易次数",
      value: `${task.win_count}胜/${task.loss_count}负`,
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
