/**
 * 权益曲线共享组件（lightweight-charts 单库形态，fe-chart-single-lib 组 2）。
 * Trading/Lab/BatchBacktest 共用；resolveEquityColor 随迁本文件。
 * jsdom 降级：内建 canvas 可用性检测，jsdom 下跳过初始化保留容器与空态。
 */
import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts';
import { POLARITY, ATTENTION } from '@/theme/tokens';

/** equity 点（UTC ISO 串时间轴，旧形制承继，装配时转 epoch 秒） */
export interface EquityPoint {
  timestamp: string;
  value: number;
}

/** 少于 2 点无法绘制曲线（空态与 MIN_POINTS 语义逐字承继） */
export const EQUITY_MIN_POINTS = 2;

/** 终点盈亏决定曲线色（双着色分立）：无基准金色；有基准按终点比较，持平按盈利计 */
export function resolveEquityColor(data: EquityPoint[], baseline?: number) {
  if (baseline == null) return ATTENTION.gold;
  const last = data[data.length - 1]?.value ?? baseline;
  return last >= baseline ? POLARITY.up : POLARITY.down;
}

export interface EquityCurveProps {
  data: EquityPoint[];
  /** 初始资金基准；提供时按终点盈亏着色，缺省金色 */
  baseline?: number;
  height?: number;
}

export function EquityLineChart({ data, baseline, height = 200 }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const hasData = data.length >= EQUITY_MIN_POINTS;
  // jsdom 降级：getContext("2d") 调用判空（jsdom 方法存在但返回 null，无绘制面）
  const canDraw = typeof document !== 'undefined' &&
    document.createElement('canvas').getContext('2d') != null;

  useEffect(() => {
    if (!hasData || !canDraw || !containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#94a3b8' },
    });
    chartRef.current = chart;
    const series = chart.addSeries(LineSeries, { lineWidth: 2 });
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [hasData, canDraw]);

  useEffect(() => {
    if (!hasData || !canDraw || !chartRef.current || !seriesRef.current) return;
    const series = seriesRef.current;
    // 后端三路输入信任面兜底：非法 timestamp 串经 Date.parse 产 NaN，lightweight
    // setData 对 NaN time 会 throw 崩组件树；非法 value（NaN/Infinity/非数）在
    // development 构建经 checkLineItem 断言 throw、production 构建断言剥除后静默
    // 进数据模型——两面一并剔除非法项，全部非法时空数组照常走 setData
    // 过滤一次两用：绘制与着色同取过滤后序列——原始末点 value 脏（被剔除）
    // 时若按原始 data 取末点，NaN >= baseline 恒 false 误显亏损色，而曲线
    // 实际末点是最后一个有效点
    const validData = data.filter(
      (d) => !Number.isNaN(Date.parse(d.timestamp)) && Number.isFinite(d.value),
    );
    const points = validData.map(
      (d) => ({ time: (Date.parse(d.timestamp) / 1000) as UTCTimestamp, value: d.value }),
    );
    series.setData(points);
    series.applyOptions({
      color: resolveEquityColor(validData, baseline),
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chartRef.current.priceScale('right').applyOptions({
      autoScale: true,
      scaleMargins: { top: 0.1, bottom: 0.1 },
      // 旧形制 y 轴 ±500 padding 的近似等价（百分比 margin，视觉余量语义一致）
    });
    chartRef.current.timeScale().fitContent();
  }, [data, baseline, canDraw]);

  if (!hasData) {
    return (
      <div className='flex items-center justify-center rounded-lg border border-border-default' style={{ height }}>
        <span className='text-xs text-text-muted'>数据不足，无法绘制资金曲线</span>
      </div>
    );
  }
  return <div ref={containerRef} style={{ height }} />;
}
