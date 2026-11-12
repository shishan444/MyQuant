import { POLARITY } from "@/theme/tokens";
import { ColorType, LineStyle, CrosshairMode } from "lightweight-charts";
import type { DeepPartial } from "lightweight-charts";
import type { ChartOptions } from "lightweight-charts";

/**
 * #RRGGBB + alpha → rgba() 字符串（fe-polarity-calibration 1.1 自
 * chartPalette.ts 迁入——两 change 共享前置，先实施者执行）。
 *
 * 职能：token 十六进制色到带透明度 rgba 色的纯函数换算。
 * 价值：图表衍生色（区域填充/虚线族）以 tokens 单源为基底派生，
 * 不再各自维护 rgba 字面第二实现。
 * 服务对象：chartThemes 衍生色常量与图表装配代码（原消费方
 * equityOption.ts 已改引本处）。
 * 边界：只接受 #RRGGBB 形制（七字符），其他形制输出不可预期。
 */
export function withAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Dark theme configuration for TradingView Lightweight Charts.
 * Uses semi-transparent dark background, muted text, and dotted grid lines.
 */
export const DARK_CHART_THEME: DeepPartial<ChartOptions> = {
  layout: {
    background: {
      type: ColorType.Solid,
      color: "rgba(13,17,23,0.8)",
    },
    textColor: "#94a3b8",
    fontFamily: "Geist Mono, monospace",
  },
  grid: {
    vertLines: {
      color: "rgba(30,37,48,0.5)",
      style: LineStyle.Dotted,
    },
    horzLines: {
      color: "rgba(30,37,48,0.5)",
      style: LineStyle.Dotted,
    },
  },
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: {
      color: "rgba(148,163,184,0.3)",
      width: 1,
      style: LineStyle.Dashed,
      labelBackgroundColor: "#1e293b",
    },
    horzLine: {
      color: "rgba(148,163,184,0.3)",
      width: 1,
      style: LineStyle.Dashed,
      labelBackgroundColor: "#1e293b",
    },
  },
  rightPriceScale: {
    borderColor: "rgba(30,37,48,0.5)",
    textColor: "#94a3b8",
  },
  timeScale: {
    borderColor: "rgba(30,37,48,0.5)",
    timeVisible: true,
    secondsVisible: false,
  },
};

/**
 * Color palette for chart series and overlays.
 */
/** 数据系列区分色族（fe-asset-data-domain 6.1 自 constants.ts 迁入——
 *  周期档色/EMA 线色为系列区分语义，tokens 无对应族；集中白名单单源） */
export const MTF_TIMEFRAME_COLORS: Record<string, string> = {
  "15m": "#3B82F6",
  "1h": "#10B981",
  "4h": "#F59E0B",
  "1d": "#8B5CF6",
  "3d": "#EF4444",
};

export const EMA_DEFAULT_COLORS = [
  "#3B82F6",
  "#10B981",
  "#F59E0B",
  "#8B5CF6",
  "#EF4444",
  "#EC4899",
] as const;

export const CHART_INDICATOR_SERIES_COLORS = {
  ema: ["#3B82F6", "#10B981", "#F59E0B"] as string[],
  boll: "#F59E0B",
};

export const CHART_COLORS = {
  /** Bullish candle body/wick color */
  candleUp: POLARITY.up, /* T2：蜡烛涨色单源 tokens */
  /** Bearish candle body/wick color */
  candleDown: POLARITY.down, /* T2：蜡烛跌色单源 tokens */
  /** Fast EMA line color */
  emaFast: "#1E88E5",
  /** Slow EMA line color */
  emaSlow: "#FF9800",
  /** RSI line color */
  rsi: "#7C4DFF",
  /** RSI overbought zone fill (70 level) */
  overbought: withAlpha(POLARITY.down, 0.3),
  /** RSI oversold zone fill (30 level) */
  oversold: withAlpha(POLARITY.up, 0.3),
  /** Buy signal marker color */
  buySignal: POLARITY.up,
  /** Sell signal marker color */
  sellSignal: POLARITY.down,
} as const;
