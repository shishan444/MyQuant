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

/**
 * 时间戳格式化四档单函数（fe-recorddetail-td-deadtfs 组 5 REQ-3d）。
 *
 * 职能：UTC ISO 串截断为四档显示形制（全部 UTC 串处理非本地时区）。
 * 四档："minute"=YYYY-MM-DD HH:mm（T→空格）；"second"=含秒截断；
 * "full"=完整串仅 T→空格；"date"=YYYY-MM-DD。null/undefined→"—"。
 * 服务对象：验证记录/交易/验证执行等页面的时间列显示收编点。
 * 边界：非 ISO 串输出未定义（调用方保证输入为 ISO 形制）；本地时区
 * 显示语义不在本函数（lib/formatDateTime 已退役）。
 */
export function formatTimestamp(
  ts: string | null | undefined,
  form: "minute" | "second" | "full" | "date",
): string {
  if (ts == null) return "\u2014";
  switch (form) {
    case "minute":
      return ts.slice(0, 16).replace("T", " ");
    case "second":
      return ts.slice(0, 19).replace("T", " ");
    case "full":
      return ts.replace("T", " ");
    case "date":
      return ts.slice(0, 10);
  }
}

// （monthsAgo/todayISO 日期预设工具随批量回测域与 verify 发起器退役
//  零消费删除——fix-replay-gap-alignment U4/U8 批次收口）
