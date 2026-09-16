/**
 * 权益曲线 option 构造（纯函数，L1 直测入口）。
 */
import type { ChartOption } from "./echartsCore";
import { CHART_PALETTE, withAlpha } from "./chartPalette";

export interface EquityPoint {
  timestamp: string;
  value: number;
}

/** 少于 2 点无法绘制曲线 */
export const EQUITY_MIN_POINTS = 2;

function formatTime(ts: string): string {
  return ts.slice(5, 16).replace("T", " ");
}

function formatMoney(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

/** 终点盈亏决定曲线色：无基准时金色（持平按盈利计） */
export function resolveEquityColor(data: EquityPoint[], baseline?: number): string {
  if (baseline == null) return CHART_PALETTE.gold;
  const last = data[data.length - 1]?.value ?? baseline;
  return last >= baseline ? CHART_PALETTE.profit : CHART_PALETTE.loss;
}

export function buildEquityOption(data: EquityPoint[], baseline?: number): ChartOption {
  const color = resolveEquityColor(data, baseline);
  return {
    animation: false,
    grid: { top: 8, right: 8, left: 8, bottom: 0, containLabel: true },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: data.map((d) => d.timestamp),
      axisLabel: {
        formatter: (value: string) => formatTime(value),
        color: CHART_PALETTE.axisLabel,
        fontSize: 10,
      },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      min: (extent: { min: number; max: number }) => extent.min - 500,
      max: (extent: { min: number; max: number }) => extent.max + 500,
      axisLabel: {
        formatter: (value: number) => formatMoney(value),
        color: CHART_PALETTE.axisLabel,
        fontSize: 10,
      },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1a1a2e",
      borderColor: "#2a2a3e",
      borderWidth: 1,
      textStyle: { color: "#e2e8f0", fontSize: 12 },
      extraCssText: "border-radius:8px;",
      formatter: (params) => {
        const first = Array.isArray(params) ? params[0] : params;
        const point = data[first.dataIndex];
        if (!point) return "";
        return [
          `<div>${formatTime(point.timestamp)}</div>`,
          `<div>Equity: ${formatMoney(point.value)}</div>`,
        ].join("");
      },
    },
    series: [
      {
        type: "line",
        data: data.map((d) => d.value),
        showSymbol: false,
        smooth: true,
        lineStyle: { color, width: 1.5 },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: withAlpha(color, 0.3) },
              { offset: 1, color: withAlpha(color, 0) },
            ],
          },
        },
      },
    ],
  };
}
