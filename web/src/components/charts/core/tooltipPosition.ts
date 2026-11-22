/**
 * 悬停浮层定位纯函数（fix-chart-tooltip-restore 组 1 新建）。
 *
 * 职能：给定悬停点/浮层实测尺寸/容器实测尺寸，计算浮层绝对定位——
 * 上方不足翻 bottom、left 双向钳制、窄容器退化贴右。
 * 价值：EquityLineChart 浮层定位以「翻转+钳制」算法（尺寸作参数）
 * 一处实现；纯计算零 DOM 依赖可直测。
 * 服务对象：EquityLineChart 的悬停浮层 style（返回值直接作绝对定位）。
 * 边界：top 不做下界钳制——placement 翻转已保证容器内，两难场景
 * （浮层高度超出容器）属容器级布局异常，不在本工具责任域。
 */

export interface TooltipPositionInput {
  /** 悬停点像素坐标（param.point，相对图表容器左上角） */
  anchor: { x: number; y: number };
  /** 浮层实测尺寸（渲染后 measure，非预估常量） */
  tooltip: { width: number; height: number };
  /** 容器实测尺寸 */
  container: { width: number; height: number };
  /** 浮层与悬停点间距，默认 8 */
  gap?: number;
  /** 容器内边留白，默认 4 */
  edgeMargin?: number;
}

export interface TooltipPositionResult {
  /** 浮层左边相对容器 x 坐标 */
  left: number;
  /** 浮层上边相对容器 y 坐标 */
  top: number;
  /** 实际翻转方向 */
  placement: "top" | "bottom";
}

export function computeTooltipPosition(
  input: TooltipPositionInput,
): TooltipPositionResult {
  const { anchor, tooltip, container } = input;
  const gap = input.gap ?? 8;
  const edgeMargin = input.edgeMargin ?? 4;

  // placement 判定：上方可容纳（含等号）置顶，不足翻下方
  const placement: "top" | "bottom" =
    anchor.y - gap - tooltip.height >= 0 ? "top" : "bottom";
  const top =
    placement === "top"
      ? anchor.y - gap - tooltip.height
      : anchor.y + gap;

  // left 双向钳制：先左钳（不低于内边距）后右钳（不越右内边距）；
  // 退化（浮层宽于容器，右界 < 左界）时 min 取右界——贴右，保证
  // left + width <= container.width - edgeMargin 恒成立
  const left = Math.min(
    Math.max(anchor.x - tooltip.width / 2, edgeMargin),
    container.width - tooltip.width - edgeMargin,
  );

  return { left, top, placement };
}
