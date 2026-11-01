/**
 * 03 策略验证·三门槛点行与星级组件（alignment-batch D1）。
 * 消费 verify 历史行扩展字段（total_trades/thresholds/star）——
 * 后端 pydantic 已声明（api/schemas.py VerifyHistoryItem），前端
 * 生成类型未同步，此处以交叉类型补形，生成层刷新后字段重合无冲突。
 * 纯函数（classifyThresholds/summarizeThresholds/formatDrawdown）在
 * thresholdsUtils.ts（react-refresh：组件文件只导出组件）。
 */
import { cn } from "@/lib/utils";
import type { VerifyHistoryItem } from "@/types/api";
import { summarizeThresholds } from "./thresholdsUtils";

/** 三门槛布尔 ← 后端 thresholds dict（指标缺失为 null，缺失≠false） */
export interface VerifyThresholds {
  return_positive: boolean | null;
  drawdown_ok: boolean | null;
  trades_enough: boolean | null;
}

/** 历史结果行补形：生成类型缺三新字段，运行时后端必给（thresholds 可 null） */
export type VerifyHistoryRow = VerifyHistoryItem & {
  total_trades?: number;
  thresholds?: VerifyThresholds | null;
  star?: number;
};

/** 门槛定义：呈现 label 与判定式文案（常量单源在后端 DEFAULT_PROFILE，此处仅展示） */
const THRESHOLD_DEFS = [
  { key: "return_positive", rule: "收益>0" },
  { key: "drawdown_ok", rule: "回撤<25%" },
  { key: "trades_enough", rule: "交易数≥5" },
] as const;

function dotTitle(rule: string, value: boolean | null): string {
  if (value === true) return `${rule} ✓ 达标`;
  if (value === false) return `${rule} ✗ 未达标`;
  return `${rule} ？ 指标缺失，无法判定`;
}

/** 三门槛点行：true=绿 / false=红 / null=灰，title 披露判定式与结果 */
export function ThresholdDots({ thresholds }: { thresholds?: VerifyThresholds | null }) {
  return (
    <span className="inline-flex items-center gap-1.5" data-testid="threshold-dots">
      {THRESHOLD_DEFS.map(({ key, rule }) => {
        const value = thresholds ? thresholds[key] : null;
        return (
          <span
            key={key}
            data-testid={`threshold-dot-${key}`}
            data-state={value === true ? "pass" : value === false ? "fail" : "missing"}
            title={dotTitle(rule, value)}
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              value === true && "bg-emerald-400",
              value === false && "bg-red-400",
              value === null && "bg-slate-600"
            )}
          />
        );
      })}
    </span>
  );
}

/** 星级：star 为门槛达标数机械换算（0-3），亮星重复 star 次 + 暗星补满 3 槽 */
export function VerifyStar({ star }: { star?: number }) {
  const lit = Math.max(0, Math.min(3, star ?? 0));
  return (
    <span
      className="inline-flex items-center text-[11px] leading-none"
      data-testid="verify-star"
      title={`星级 ${lit}/3（门槛达标数机械换算）`}
    >
      {Array.from({ length: 3 }, (_, i) => (
        <span
          key={i}
          data-testid={i < lit ? "verify-star-lit" : "verify-star-dim"}
          className={i < lit ? "text-amber-400" : "text-slate-700"}
        >
          ★
        </span>
      ))}
    </span>
  );
}

/** 三档归类与纯计算在 thresholdsUtils.ts（react-refresh 分离） */

/** 结果集层面的三态汇总计数行 */
export function ThresholdsSummary({ rows }: { rows: VerifyHistoryRow[] }) {
  const { pass, fail, inconclusive } = summarizeThresholds(rows);
  return (
    <div
      className="flex items-center gap-3 text-[11px]"
      data-testid="thresholds-summary"
    >
      <span className="text-text-secondary">门槛三态：</span>
      <span className="text-emerald-400" data-testid="thresholds-summary-pass">
        达标 {pass}
      </span>
      <span className="text-red-400" data-testid="thresholds-summary-fail">
        未达标 {fail}
      </span>
      <span className="text-slate-400" data-testid="thresholds-summary-inconclusive">
        缺失 {inconclusive}
      </span>
    </div>
  );
}

