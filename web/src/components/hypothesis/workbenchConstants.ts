/**
 * 假设工作台常量与工具（hypothesis-workbench 组 3）——从 HypothesisStepper
 * 拆出（审查闸门：react-refresh/only-export-components——组件文件只留
 * 组件导出，常量/工具独立文件恢复 fast refresh；非测试消费方零，拆分
 * 对生产代码无影响）。
 */

/** 02-B1 五步流水线步骤名（单源——步骤推进由页面业务态驱动） */
export const WORKBENCH_STEPS = ["构建", "预检", "案例", "沙盒", "登记"] as const;

export type WorkbenchStep = (typeof WORKBENCH_STEPS)[number];

export function stepIndex(name: WorkbenchStep): number {
  return WORKBENCH_STEPS.indexOf(name);
}

/**
 * 02-B2-4 风控 sl_mode 四档选项（单源消费 dna.py SL_MODES——
 * 母案定案：SL_MODES=("pct","atr","trail","series") 唯一操作定义；
 * series 档附 sl_series_source 声明提示）。
 */
export const SL_MODE_OPTIONS: Array<{
  value: "pct" | "atr" | "trail" | "series";
  label: string;
  hint: string;
}> = [
  { value: "pct", label: "pct", hint: "百分比止损" },
  { value: "atr", label: "atr", hint: "ATR 波动倍数" },
  { value: "trail", label: "trail", hint: "移动止损（跟随峰值）" },
  { value: "series", label: "series", hint: "价格列止损线（sl_series_source 声明——仅回测域）" },
];
