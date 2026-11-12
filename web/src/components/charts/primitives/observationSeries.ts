import { LineSeries } from "lightweight-charts";
import type { IChartApi, ISeriesApi, LineWidth, LineStyle, UTCTimestamp } from "lightweight-charts";
import { toTime } from "../core/time";

/**
 * G-标注原语 · 观测序列族（hypothesis-workbench 审查 CARD-01）——
 * C4 series 止损线与 C5 trail 观测序列进同一原语族的不同数据接口
 * （母案 §8 宣称至此落地——此前 Lab/TradingChart 两处内联实现未进族）。
 */
export interface ObservationPoint {
  time: string;
  value: number | null; // null=SL 未定义期（后端 NaN→null 传输），不画
}

/** 样式差异参数（两消费方缺省同款：虚线黄观测语义） */
export interface ObservationSeriesStyle {
  color?: string;
  lineWidth?: LineWidth;
  lineStyle?: LineStyle;
  title?: string;
}

const DEFAULT_STYLE = {
  color: "#FBBF24", lineWidth: 1, lineStyle: 2,
} as const;

/**
 * 挂载单条观测序列线（纯挂载通道，无生命周期管理——挂/重挂/清由
 * 消费方经自己的句柄通道驱动）。
 *
 * NaN/null 点产 whitespace 项（观测值未定义期真断线——与 docstring
 * 宣称一致的实现；修正前 filter 剔除后剩余点被连成线）；无有效点或
 * 挂载失败返回 null（观测面容错——不阻塞主图，异常静默由消费方
 * 注释承接）。
 */
export function attachObservationSeries(
  chart: IChartApi,
  points: ObservationPoint[],
  style?: ObservationSeriesStyle,
): ISeriesApi<"Line"> | null {
  const valid = points.filter(
    (p) => p.value != null && Number.isFinite(p.value),
  );
  if (valid.length === 0) return null;
  try {
    const series = chart.addSeries(LineSeries, {
      color: style?.color ?? DEFAULT_STYLE.color,
      lineWidth: style?.lineWidth ?? DEFAULT_STYLE.lineWidth,
      lineStyle: style?.lineStyle ?? DEFAULT_STYLE.lineStyle,
      priceLineVisible: false,
      lastValueVisible: false,
      title: style?.title ?? "",
    });
    series.setData(
      // 无效点产 whitespace 项（无 value 键）——未定义期断线不画
      points.map((p) =>
        p.value != null && Number.isFinite(p.value)
          ? { time: toTime(p.time) as UTCTimestamp, value: p.value }
          : { time: toTime(p.time) as UTCTimestamp },
      ),
    );
    return series;
  } catch {
    return null; // 挂载失败不抛（观测面容错）
  }
}
