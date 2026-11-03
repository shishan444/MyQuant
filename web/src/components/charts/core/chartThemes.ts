import { POLARITY } from "@/theme/tokens";
import { ColorType, LineStyle, CrosshairMode } from "lightweight-charts";
import type { DeepPartial } from "lightweight-charts";
import type { ChartOptions } from "lightweight-charts";

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
  overbought: "rgba(255,23,68,0.3)",
  /** RSI oversold zone fill (30 level) */
  oversold: "rgba(0,200,83,0.3)",
  /** Buy signal marker color */
  buySignal: POLARITY.up,
  /** Sell signal marker color */
  sellSignal: POLARITY.down,
} as const;
