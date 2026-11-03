import { POLARITY } from "@/theme/tokens";
import type { ISeriesApi, IPriceLine, Time } from "lightweight-charts";

/**
 * G-标注原语 · 价线族（kline-common-layer 组 4）——入场线/止损线。
 * 载体=官方 createPriceLine 通道（稳定可测：priceLines() 数组可断言）。
 * C4 series 序列与 C5 trail 观测序列进同一原语族的不同数据接口（母案 §8）。
 */
export interface EntryLineData {
  price: number;
  label: string;
}

export type StopLossKind = "fixed" | "trail";

export interface StopLossLineData {
  price: number;
  kind: StopLossKind;
  /** trail 档=虚线；fixed 档=实线（07-B4-2：保护线仅 sl_mode=trail 时显示——visible 由消费方控制） */
  visible: boolean;
}

/** 挂载入场线：返回价线控制器（remove 摘除） */
export function attachEntryLine(series: ISeriesApi<"Candlestick", Time>, data: EntryLineData): IPriceLine {
  return series.createPriceLine(toEntryLineOptions(data));
}

/** 挂载止损线：trail 虚线/fixed 实线；不可见档不挂载（返回 null） */
export function attachStopLossLine(
  series: ISeriesApi<"Candlestick", Time>,
  data: StopLossLineData,
): IPriceLine | null {
  if (!data.visible) return null;
  return series.createPriceLine(toStopLossLineOptions(data));
}

/** 纯函数层：数据 → createPriceLine options（独立可测，不依赖图表实例） */
export function toEntryLineOptions(data: EntryLineData) {
  return {
    price: data.price,
    color: "#F59E0B",
    lineWidth: 1 as const,
    lineStyle: 0,
    axisLabelVisible: true,
    title: data.label,
  };
}

export function toStopLossLineOptions(data: StopLossLineData) {
  return {
    price: data.price,
    color: data.kind === "trail" ? "#FBBF24" : POLARITY.down, /* T2：非涨跌护栏语义沿 STATE.warn 金/trail 与 POLARITY.down */
    lineWidth: 1 as const,
    // LineStyle.Dashed=2（trail 档虚线——移动止损的「跟随」语义视觉区分）
    lineStyle: data.kind === "trail" ? 2 : 0,
    axisLabelVisible: true,
    title: data.kind === "trail" ? "SL(trail)" : "SL",
  };
}
