import { cn } from "@/lib/utils";
import {
  WORKBENCH_STEPS,
  SL_MODE_OPTIONS,
} from "@/components/hypothesis/workbenchConstants";

/**
 * 02-B1 五步流水线条（hypothesis-workbench 组 3.1）——假设工作台步骤态机：
 * 构建→预检→案例→沙盒→登记。当前步高亮、已完成步可回跳、未达步禁用。
 * 消费方 Lab 页（/lab）；步骤推进由页面业务态驱动（本组件纯呈现）。
 * 常量/工具在 workbenchConstants（审查闸门拆分——fast refresh 恢复；
 * re-export 仍触 only-export-components，消费方直接 import 新路径）。
 */

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
 * 02-B2-4 风控 sl_mode 四档选择器（选项单源 workbenchConstants.
 * SL_MODE_OPTIONS——消费 dna.py SL_MODES 母案定案）。
 */

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
