/**
 * 权益曲线共享组件（echarts 面积图）。
 *
 * 全库唯一独立权益曲线实现：Trading（基准着色）与 Lab/BatchBacktest/
 * Evolution 回测抽屉（金色曲线）共用。KlineChart 的 equity 内嵌子图是
 * K 线形态的一部分，不属于本组件管辖。option 构造见 core/equityOption。
 */
import { useEffect, useRef } from "react";
import { echarts } from "./core/echartsCore";
import {
  buildEquityOption,
  EQUITY_MIN_POINTS,
  type EquityPoint,
} from "./core/equityOption";

export interface EquityCurveProps {
  data: EquityPoint[];
  /** 初始资金基准；提供时按终点盈亏着色（盈利绿/亏损红），缺省金色 */
  baseline?: number;
  height?: number;
}

export function EquityCurve({ data, baseline, height = 200 }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const hasData = data.length >= EQUITY_MIN_POINTS;

  useEffect(() => {
    if (!hasData || !containerRef.current) return;
    const chart = echarts.init(containerRef.current);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [hasData]);

  useEffect(() => {
    if (!hasData || !chartRef.current) return;
    chartRef.current.setOption(buildEquityOption(data, baseline), { notMerge: true });
  }, [hasData, data, baseline]);

  if (!hasData) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-border-default"
        style={{ height }}
      >
        <span className="text-xs text-text-muted">数据不足，无法绘制资金曲线</span>
      </div>
    );
  }

  return <div ref={containerRef} style={{ height }} />;
}
