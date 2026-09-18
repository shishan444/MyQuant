import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { FITNESS_COLOR_THRESHOLDS } from "@/lib/constants";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function getFitnessColor(
  fitness: number,
  objective: string = "sharpe",
  targetScore?: number,
): string {
  if (fitness == null || isNaN(fitness)) return "text-slate-500";
  const thresholds = FITNESS_COLOR_THRESHOLDS[objective] ?? FITNESS_COLOR_THRESHOLDS.sharpe;
  const good = objective === "annual_return" && targetScore != null && targetScore > 0
    ? targetScore
    : thresholds.good;
  const mid = objective === "annual_return" && targetScore != null && targetScore > 0
    ? targetScore * 0.5
    : thresholds.mid;
  if (fitness >= good) return "text-emerald-400";
  if (fitness >= mid) return "text-amber-400";
  return "text-red-400";
}

export function formatPercent(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

export function formatCurrency(value: number): string {
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

export function formatNumber(value: number, decimals = 2): string {
  return value.toFixed(decimals);
}

export function formatPercentValue(value: number): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function formatDuration(start: string, end: string): string {
  try {
    const ms = new Date(end).getTime() - new Date(start).getTime();
    if (ms < 60000) return `${Math.round(ms / 1000)}秒`;
    if (ms < 3600000) return `${Math.round(ms / 60000)}分钟`;
    return `${(ms / 3600000).toFixed(1)}小时`;
  } catch {
    return "-";
  }
}

export function formatDateTime(t: string): string {
  try {
    return new Date(t).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return t;
  }
}

/** N 个月前的 ISO 日期（YYYY-MM-DD）——验证/批量回测日期预设共用（G7 归位 lib） */
export function monthsAgo(m: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - m);
  return d.toISOString().slice(0, 10);
}

/** 今日 ISO 日期（YYYY-MM-DD） */
export function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}
