/**
 * 03 策略验证·三门槛纯函数（alignment-batch D1）。
 * 与组件分离（react-refresh：组件文件只导出组件——workbenchConstants
 * 先例）；常量单源在后端 DEFAULT_PROFILE，此处仅展示语义的纯计算。
 */
import type { VerifyThresholds } from "./VerifyThresholds";

/** 三档归类：任一 false=明确未达标（优先于缺失）；否则任一 null=缺失；否则达标 */
export type ThresholdVerdict = "pass" | "fail" | "inconclusive";

export function classifyThresholds(thresholds?: VerifyThresholds | null): ThresholdVerdict {
  if (!thresholds) return "inconclusive";
  const values = [thresholds.return_positive, thresholds.drawdown_ok, thresholds.trades_enough];
  if (values.some((v) => v === false)) return "fail";
  if (values.some((v) => v === null)) return "inconclusive";
  return "pass";
}

export function summarizeThresholds(rows: Array<{ thresholds?: VerifyThresholds | null }>): {
  pass: number;
  fail: number;
  inconclusive: number;
} {
  const counts = { pass: 0, fail: 0, inconclusive: 0 };
  for (const row of rows) counts[classifyThresholds(row.thresholds)]++;
  return counts;
}

/** 回撤口径归一：后端落库为负值小数（drawdown.min() 直落，-0.08=8%），展示取 abs+百分数 */
export function formatDrawdown(v: number): string {
  return `${(Math.abs(v) * 100).toFixed(1)}%`;
}
