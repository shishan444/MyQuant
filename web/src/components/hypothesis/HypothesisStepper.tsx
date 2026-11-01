import { cn } from "@/lib/utils";

/**
 * 02-B1 五步流水线条（hypothesis-workbench 组 3.1）——假设工作台步骤态机：
 * 构建→预检→案例→沙盒→登记。当前步高亮、已完成步可回跳、未达步禁用。
 * 消费方 Lab 页（/lab）；步骤推进由页面业务态驱动（本组件纯呈现）。
 */
export const WORKBENCH_STEPS = ["构建", "预检", "案例", "沙盒", "登记"] as const;

export type WorkbenchStep = (typeof WORKBENCH_STEPS)[number];

export function stepIndex(name: WorkbenchStep): number {
  return WORKBENCH_STEPS.indexOf(name);
}

interface HypothesisStepperProps {
  current: number;
  /** 已完成的最远步骤（可回跳上界）；缺省=current */
  furthest?: number;
  onStepClick?: (idx: number) => void;
}

export function HypothesisStepper({ current, furthest, onStepClick }: HypothesisStepperProps) {
  const maxReachable = furthest ?? current;
  return (
    <nav aria-label="假设流水线步骤" className="flex items-center gap-1 py-2">
      {WORKBENCH_STEPS.map((name, i) => {
        const isCurrent = i === current;
        const isDone = i < current;
        const reachable = i <= maxReachable;
        return (
          <button
            key={name}
            type="button"
            disabled={!reachable}
            onClick={() => reachable && onStepClick?.(i)}
            className={cn(
              "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors",
              isCurrent && "bg-accent-gold/10 text-accent-gold border border-accent-gold/50",
              isDone && !isCurrent && "text-profit hover:bg-white/5 border border-transparent",
              !isCurrent && !isDone && "text-text-muted border border-transparent",
              !reachable && "cursor-not-allowed opacity-50",
            )}
          >
            <span className={cn("font-mono", isCurrent ? "text-accent-gold" : isDone ? "text-profit" : "text-text-muted")}>
              {isDone ? "✓" : i + 1}
            </span>
            {name}
          </button>
        );
      })}
    </nav>
  );
}

/**
 * 02-B2-4 风控 sl_mode 四档选择器（单源消费 dna.py SL_MODES——
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

interface SlModeSelectProps {
  value: string;
  onChange: (v: "pct" | "atr" | "trail" | "series") => void;
}

export function SlModeSelect({ value, onChange }: SlModeSelectProps) {
  const active = SL_MODE_OPTIONS.find((o) => o.value === value);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1" role="radiogroup" aria-label="sl_mode 四档">
        {SL_MODE_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={o.value === value}
            onClick={() => onChange(o.value)}
            className={cn(
              "rounded px-2 py-0.5 text-[11px] font-mono transition-colors",
              o.value === value
                ? "bg-accent-gold/15 text-accent-gold border border-accent-gold/50"
                : "bg-slate-800/30 text-text-secondary hover:text-text-primary border border-transparent",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
      {active && <p className="text-[10px] text-text-muted">{active.hint}</p>}
    </div>
  );
}
