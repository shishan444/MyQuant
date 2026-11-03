/**
 * 图表专用色板（echarts）。
 *
 * G4 冻结：图表色不入 theme token、不读 CSS 变量，以集中常量维护；
 * 语义色取值与 antdThemeConfig 对齐（colorSuccess/colorError/colorPrimary）。
 * lightweight-charts 侧配色见 chartThemes.ts（两库互不读取）。
 */
import { POLARITY, ATTENTION } from "@/theme/tokens";

export const CHART_PALETTE = {
  /** 盈利（对齐 colorSuccess） */
  profit: POLARITY.up, /* T2：随 colorSuccess 语义对齐 teal */
  /** 亏损（对齐 colorError） */
  loss: POLARITY.down, /* T2：随 colorError 语义对齐 rose */
  /** 金/品牌主色（对齐 colorPrimary），无基准资金时的曲线色 */
  gold: ATTENTION.gold,
  /** 综合得分线 */
  scoreLine: ATTENTION.gold,
  /** 适应度线 */
  fitnessLine: POLARITY.up,
  /** 累计最佳线（半透明绿） */
  cumulativeLine: "rgba(45,212,191,0.5)", /* T2 衍生 */
  /** 平均得分线（半透明紫） */
  avgLine: "rgba(168,85,247,0.4)",
  /** 最优更新菱形标记 */
  champion: POLARITY.up,
  /** 种群边界竖线（半透明青） */
  boundary: "rgba(6,182,212,0.25)",
  /** 目标线（无适应度数据时的回退） */
  targetLine: "#94A3B8",
  /** 轴文字 */
  axisLabel: "#64748B",
  /** 网格线 */
  gridLine: "rgba(100,116,139,0.15)",
} as const;

/** #RRGGBB + alpha → rgba() 字符串 */
export function withAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
