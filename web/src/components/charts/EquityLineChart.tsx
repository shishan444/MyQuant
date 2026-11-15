/**
 * 权益曲线共享组件（lightweight-charts 单库形态，fe-chart-single-lib 组 2）。
 * Trading/Lab/BatchBacktest 共用；resolveEquityColor 随迁本文件。
 * jsdom 降级：内建 canvas 可用性检测，jsdom 下跳过初始化保留容器与空态。
 * 悬停浮层（fix-chart-tooltip-restore F1）：subscribeCrosshairMove 订阅，
 * 浮层只消费事件回调与 props 两源数据（R-A），全 JSX 渲染零 HTML 注入面（R-D）。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createChart, AreaSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, MouseEventParams, UTCTimestamp } from 'lightweight-charts';
import { withAlpha } from '@/components/charts/core/chartThemes';
import {
  computeTooltipPosition,
  type TooltipPositionResult,
} from '@/components/charts/core/tooltipPosition';
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

/** 悬停时间行（旧 equityOption.ts:16-18 逐字恢复）：原始串字面切片 MM-DD HH:mm。
 * 对后端三种串形制（naive 无 Z/带 Z/空格分隔）恒与旧版字面一致（D3 反查链） */
export function formatTime(ts: string): string {
  return ts.slice(5, 16).replace("T", " ");
}

/** 悬停金额三档缩写（旧 equityOption.ts:20-24 逐字恢复，Math.abs 判档）：
 * 百万档两位小数 M/千档一位小数 K/其余整数无后缀；负号夹在 $ 与数字间 */
export function formatMoney(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

/** 悬停态存原始数据引用（格式化在 JSX 内完成，回调薄：只反查） */
interface EquityHoverState {
  timestamp: string;
  value: number;
  x: number;
  y: number;
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
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  // 悬停浮层三通道：validDataRef（数据 effect 写入、订阅回调读——订阅挂初始化
  // effect 只跑一次而数据随 props 重跑，直接闭包捕获会陈旧）、hover state（存
  // 原始引用）、tooltipPos（measure 后的绝对定位值）
  const validDataRef = useRef<EquityPoint[]>([]);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<EquityHoverState | null>(null);
  const [tooltipPos, setTooltipPos] = useState<TooltipPositionResult | null>(null);
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
    // AreaSeries 承载线+面积渐变（旧 echarts areaStyle 形制随迁）：渐变三色
    // 属动态着色（resolveEquityColor 依赖 baseline），统一在数据 effect 的
    // applyOptions 设置——两 effect 同轮 commit 内先后执行，首帧前已就位
    const series = chart.addSeries(AreaSeries, { lineWidth: 2 });
    seriesRef.current = series;
    // 悬停订阅挂初始化 effect（D1：与图表实例同生共死，数据 effect 重跑不重复
    // 订阅）。离图（time/point 任一 undefined）清态；seriesData 无值或 epoch 反查
    // 未命中走防御态不更新（吸附语义下理论不可达的兜底）
    chart.subscribeCrosshairMove((param: MouseEventParams) => {
      if (param.time === undefined || param.point === undefined) {
        setHover(null);
        return;
      }
      const current = seriesRef.current;
      if (!current) return;
      const point = param.seriesData.get(current) as
        | { value?: number }
        | undefined;
      if (!point || point.value === undefined || !Number.isFinite(point.value)) {
        return;
      }
      // 时间反查与 setData 同用 Date.parse 直调（勿引 time.ts toTime——裸串补
      // Z 按 UTC 解析与 Date.parse 本地时区解析在 naive 串上产出不同 epoch，
      // 误引则谓词失配恒不命中）；显示走原始串字面切片（formatTime）
      const hit = validDataRef.current.find(
        (d) => Date.parse(d.timestamp) / 1000 === param.time,
      );
      if (!hit) return;
      setHover({
        timestamp: hit.timestamp,
        value: point.value,
        x: param.point.x,
        y: param.point.y,
      });
    });
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
    // ref 通道：同一过滤后序列供订阅回调反查（与绘制同源——原始数组含被过滤脏点）
    validDataRef.current = validData;
    const points = validData.map(
      (d) => ({ time: (Date.parse(d.timestamp) / 1000) as UTCTimestamp, value: d.value }),
    );
    series.setData(points);
    // AreaSeries 线条色选项名是 lineColor（非 LineSeries 的 color）；渐变
    // 顶色 0.3 透明度→底色全透明随线色三态（up/down/gold）动态更新
    const lineColor = resolveEquityColor(validData, baseline);
    series.applyOptions({
      lineColor,
      topColor: withAlpha(lineColor, 0.3),
      bottomColor: withAlpha(lineColor, 0),
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

  // 浮层定位（消费 charts/core/tooltipPosition 纯函数，锚点=param.point）：
  // 渲染后 measure 实测尺寸再计算——useLayoutEffect 在 paint 前完成定位更新，
  // 无闪烁；jsdom 下 offsetWidth 恒 0（宽高传反类缺陷归 e2e G-1 真布局终审）
  useLayoutEffect(() => {
    if (!hover || !tooltipRef.current || !containerRef.current) {
      setTooltipPos(null);
      return;
    }
    setTooltipPos(
      computeTooltipPosition({
        anchor: { x: hover.x, y: hover.y },
        tooltip: {
          width: tooltipRef.current.offsetWidth,
          height: tooltipRef.current.offsetHeight,
        },
        container: {
          width: containerRef.current.offsetWidth,
          height: containerRef.current.offsetHeight,
        },
      }),
    );
  }, [hover]);

  if (!hasData) {
    return (
      <div className='flex items-center justify-center rounded-lg border border-border-default' style={{ height }}>
        <span className='text-xs text-text-muted'>数据不足，无法绘制资金曲线</span>
      </div>
    );
  }
  return (
    <div
      ref={containerRef}
      data-testid="equity-chart-container"
      style={{ height, position: 'relative' }}
    >
      {hover && (
        <div
          ref={tooltipRef}
          data-testid="equity-tooltip"
          style={{
            position: 'absolute',
            left: tooltipPos?.left,
            top: tooltipPos?.top,
            zIndex: 10,
            pointerEvents: 'none',
            // 旧 tooltip 外层样式随迁（§1.2 Equity 侧清单，A1 视觉近似面）
            backgroundColor: '#1a1a2e',
            border: '1px solid #2a2a3e',
            borderRadius: 8,
            color: '#e2e8f0',
            fontSize: 12,
            padding: 8,
            whiteSpace: 'nowrap',
          }}
        >
          <div>{formatTime(hover.timestamp)}</div>
          <div>{`Equity: ${formatMoney(hover.value)}`}</div>
        </div>
      )}
    </div>
  );
}
