import { useState, useRef } from "react";
import { cn } from "@/lib/utils";
import { DropdownPortal } from "./DropdownPortal";

interface TargetInputProps {
  value: string;
  onChange: (v: string) => void;
  action: string;
  subject: string;
  baseTimeframe: string;
  referencedTimeframes: string[];
}

const INDICATOR_TARGETS = [
  { value: "bb_upper", label: "布林带上轨" },
  { value: "bb_middle", label: "布林带中轨" },
  { value: "bb_lower", label: "布林带下轨" },
  { value: "ema", label: "EMA" },
  { value: "sma", label: "SMA" },
  { value: "rvol", label: "RVOL" },
  { value: "vwma", label: "VWMA" },
  { value: "aroon_up", label: "Aroon Up" },
  { value: "aroon_down", label: "Aroon Down" },
];

const CROSS_TF_INDICATORS = [
  { value: "ema_20", label: "EMA(20)" },
  { value: "ema_50", label: "EMA(50)" },
  { value: "bb_upper", label: "BOLL上轨" },
  { value: "bb_lower", label: "BOLL下轨" },
];

export function getTargetLabel(value: string): string {
  const found = INDICATOR_TARGETS.find((t) => t.value === value);
  if (found) return found.label;
  if (value === "" || value === undefined) return "选择参考值";
  if (!isNaN(Number(value))) return `${value}%`;
  // Cross-timeframe format: "4h:ema_20"
  if (value.includes(":")) {
    const [tf, indicator] = value.split(":");
    return `${tf.toUpperCase()} ${indicator}`;
  }
  return value;
}

function isCrossAction(action: string): boolean {
  return ["touch", "cross_above", "cross_below", "breakout", "breakdown"].includes(action);
}

function isMultiplierAction(action: string): boolean {
  return ["spike", "shrink"].includes(action);
}

function isConsecutiveAction(action: string): boolean {
  return ["consecutive_up", "consecutive_down"].includes(action);
}

function isLookbackAction(action: string): boolean {
  return ["lookback_any", "lookback_all"].includes(action);
}

function isSeriesCrossAction(action: string): boolean {
  return ["cross_above_series", "cross_below_series"].includes(action);
}

function isSupportResistanceAction(action: string): boolean {
  return ["touch_bounce", "role_reversal", "wick_touch"].includes(action);
}

export function TargetInput({ value, onChange, action, subject: _subject, baseTimeframe, referencedTimeframes }: TargetInputProps) {
  // Form D: consecutive actions -> count input
  if (isConsecutiveAction(action)) {
    const numVal = value || "3";
    return (
      <div className="flex items-center gap-1">
        <span className="text-xs text-text-muted">N=</span>
        <input
          type="number"
          value={numVal}
          onChange={(e) => onChange(e.target.value)}
          className="h-7 w-12 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary text-center outline-none focus:border-accent-gold"
          min="2"
          max="20"
          step="1"
        />
        <span className="text-xs text-text-muted">根K线</span>
      </div>
    );
  }

  // Form E: lookback actions -> window input
  if (isLookbackAction(action)) {
    const numVal = value || "5";
    return (
      <div className="flex items-center gap-1">
        <span className="text-xs text-text-muted">窗口=</span>
        <input
          type="number"
          value={numVal}
          onChange={(e) => onChange(e.target.value)}
          className="h-7 w-12 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary text-center outline-none focus:border-accent-gold"
          min="2"
          max="20"
          step="1"
        />
        <span className="text-xs text-text-muted">根K线</span>
      </div>
    );
  }

  // Form F：序列穿越类动作 → 指标线目标下拉
  if (isSeriesCrossAction(action)) {
    return (
      <CrossTargetDropdown
        value={value}
        onChange={onChange}
        baseTimeframe={baseTimeframe}
        referencedTimeframes={referencedTimeframes}
      />
    );
  }

  // Form G: support/resistance actions -> direction/role selector
  if (isSupportResistanceAction(action)) {
    if (action === "touch_bounce") {
      const options = [
        { value: "support", label: "支撑" },
        { value: "resistance", label: "压力" },
      ];
      const current = options.find((o) => o.value === value) || options[0];
      return (
        <div className="flex items-center gap-1">
          <span className="text-xs text-text-muted">方向:</span>
          <select
            value={current.value}
            onChange={(e) => onChange(e.target.value)}
            className="h-7 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary outline-none focus:border-accent-gold"
          >
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
      );
    }
    if (action === "role_reversal") {
      const options = [
        { value: "resistance", label: "变压力" },
        { value: "support", label: "变支撑" },
      ];
      const current = options.find((o) => o.value === value) || options[0];
      return (
        <div className="flex items-center gap-1">
          <span className="text-xs text-text-muted">角色:</span>
          <select
            value={current.value}
            onChange={(e) => onChange(e.target.value)}
            className="h-7 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary outline-none focus:border-accent-gold"
          >
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
      );
    }
    // wick_touch
    const options = [
      { value: "above", label: "上方" },
      { value: "below", label: "下方" },
    ];
    const current = options.find((o) => o.value === value) || options[0];
    return (
      <div className="flex items-center gap-1">
        <span className="text-xs text-text-muted">方向:</span>
        <select
          value={current.value}
          onChange={(e) => onChange(e.target.value)}
          className="h-7 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary outline-none focus:border-accent-gold"
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>
    );
  }

  // Form A：穿越/触碰/突破/跌破 → 选指标线
  if (isCrossAction(action)) {
    return (
      <CrossTargetDropdown
        value={value}
        onChange={onChange}
        baseTimeframe={baseTimeframe}
        referencedTimeframes={referencedTimeframes}
      />
    );
  }

  // Form C：放量/缩量 → 倍数输入
  if (isMultiplierAction(action)) {
    const numVal = value || "2";
    return (
      <div className="flex items-center gap-1">
        <span className="text-xs text-text-muted">&gt;=</span>
        <input
          type="number"
          value={numVal}
          onChange={(e) => onChange(e.target.value)}
          className="h-7 w-12 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary text-center outline-none focus:border-accent-gold"
          min="0.1"
          step="0.5"
        />
        <span className="text-xs text-text-muted">倍均量</span>
      </div>
    );
  }

  // Form B：大于/小于 → 数字输入（百分比或绝对值）
  const numVal = value || "0";
  return (
    <div className="flex items-center gap-1">
      <span className="text-xs text-text-muted">
        {action === "gt" || action === "rise" ? ">=" : "<="}
      </span>
      <input
        type="number"
        value={numVal}
        onChange={(e) => onChange(e.target.value)}
        className="h-7 w-14 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary text-center outline-none focus:border-accent-gold"
        step="0.5"
      />
      <span className="text-xs text-text-muted">%</span>
    </div>
  );
}

function CrossTargetDropdown({
  value,
  onChange,
  baseTimeframe,
  referencedTimeframes,
}: {
  value: string;
  onChange: (v: string) => void;
  baseTimeframe: string;
  referencedTimeframes: string[];
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const currentLabel = (() => {
    const found = INDICATOR_TARGETS.find((t) => t.value === value);
    if (found) return found.label;
    if (value.includes(":")) {
      const [tf, indicator] = value.split(":");
      return `${tf.toUpperCase()} ${indicator}`;
    }
    return "选择参考值";
  })();

  const crossTfs = referencedTimeframes.filter((tf) => tf !== baseTimeframe);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={cn(
          "h-7 rounded-md border px-2 text-xs transition-colors",
          "border-border-default bg-bg-surface text-text-primary",
          "hover:border-accent-gold/50",
        )}
        onClick={() => setOpen(!open)}
      >
        {currentLabel}
      </button>

      <DropdownPortal triggerRef={triggerRef} open={open} onClose={() => setOpen(false)} width={160}>
        <div
          className={cn(
            "rounded-lg border border-border-default bg-bg-surface shadow-xl",
          )}
        >
          <div className="max-h-56 overflow-y-auto py-1">
            {/* Standard indicator targets */}
            <div className="px-2 py-1 text-[10px] font-semibold text-text-muted">
              指标线
            </div>
            {INDICATOR_TARGETS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={cn(
                  "flex w-full items-center px-3 py-1.5 text-xs transition-colors",
                  value === opt.value
                    ? "bg-accent-gold/10 text-accent-gold"
                    : "text-text-primary hover:bg-white/5",
                )}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
              >
                {opt.label}
              </button>
            ))}

            {/* Cross-timeframe targets */}
            {crossTfs.length > 0 && (
              <>
                <div className="px-2 py-1 text-[10px] font-semibold text-text-muted">
                  跨周期指标
                </div>
                {crossTfs.map((tf) =>
                  CROSS_TF_INDICATORS.map((ind) => {
                    const tv = `${tf}:${ind.value}`;
                    return (
                      <button
                        key={tv}
                        type="button"
                        className={cn(
                          "flex w-full items-center px-3 py-1.5 text-xs transition-colors",
                          value === tv
                            ? "bg-accent-gold/10 text-accent-gold"
                            : "text-text-primary hover:bg-white/5",
                        )}
                        onClick={() => {
                          onChange(tv);
                          setOpen(false);
                        }}
                      >
                        {tf.toUpperCase()} {ind.label}
                      </button>
                    );
                  }),
                )}
              </>
            )}
          </div>
        </div>
      </DropdownPortal>
    </>
  );
}
