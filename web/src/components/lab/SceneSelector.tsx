/** Scene template selector with multi-select checkboxes and parameter controls. */
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import type { SceneTypeInfo } from "@/types/scene";

/** Parameter schemas for each scene type. */
const SCENE_PARAM_SCHEMAS: Record<string, Array<{
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
}>> = {
  // Sub-patterns of top_pattern share the same params as their parent
  double_top: [
    { key: "lookback", label: "回看K线数", min: 3, max: 20, step: 1, defaultValue: 5 },
    { key: "confirmation_bars", label: "确认K线数", min: 3, max: 20, step: 1, defaultValue: 5 },
    { key: "min_prominence_pct", label: "最小突起%", min: 0.1, max: 5, step: 0.1, defaultValue: 0.5 },
  ],
  head_shoulders_top: [
    { key: "lookback", label: "回看K线数", min: 3, max: 20, step: 1, defaultValue: 5 },
    { key: "confirmation_bars", label: "确认K线数", min: 3, max: 20, step: 1, defaultValue: 5 },
    { key: "min_prominence_pct", label: "最小突起%", min: 0.1, max: 5, step: 0.1, defaultValue: 0.5 },
  ],
  triple_top: [
    { key: "lookback", label: "回看K线数", min: 3, max: 20, step: 1, defaultValue: 5 },
    { key: "confirmation_bars", label: "确认K线数", min: 3, max: 20, step: 1, defaultValue: 5 },
    { key: "min_prominence_pct", label: "最小突起%", min: 0.1, max: 5, step: 0.1, defaultValue: 0.5 },
  ],
  volume_spike: [
    { key: "multiplier", label: "倍数阈值", min: 1.5, max: 5, step: 0.5, defaultValue: 2.5 },
    { key: "avg_period", label: "均量周期", min: 10, max: 60, step: 5, defaultValue: 20 },
  ],
  mean_reversion: [
    { key: "ma_period", label: "MA周期", min: 10, max: 200, step: 10, defaultValue: 50 },
    { key: "deviation_pct", label: "偏离阈值%", min: 1, max: 10, step: 0.5, defaultValue: 3.0 },
  ],
  volume_breakout: [
    { key: "volume_multiplier", label: "量比倍数", min: 1, max: 5, step: 0.5, defaultValue: 2.0 },
  ],
  support_resistance: [
    { key: "proximity_pct", label: "接近度%", min: 0.2, max: 3, step: 0.2, defaultValue: 1.0 },
  ],
  cross_timeframe: [
    { key: "fast_period", label: "快线周期", min: 5, max: 30, step: 5, defaultValue: 20 },
    { key: "slow_period", label: "慢线周期", min: 30, max: 100, step: 10, defaultValue: 50 },
  ],
};

// Sub-type color map for badge coloring
const SUBTYPE_COLORS: Record<string, string> = {
  double_top: "bg-amber-400/15 text-amber-400 border-amber-400/30",
  head_shoulders_top: "bg-purple-400/15 text-purple-400 border-purple-400/30",
  triple_top: "bg-blue-400/15 text-blue-400 border-blue-400/30",
  volume_spike: "bg-emerald-400/15 text-emerald-400 border-emerald-400/30",
  mean_reversion: "bg-sky-400/15 text-sky-400 border-sky-400/30",
  volume_breakout: "bg-rose-400/15 text-rose-400 border-rose-400/30",
  support_resistance: "bg-indigo-400/15 text-indigo-400 border-indigo-400/30",
  cross_timeframe: "bg-teal-400/15 text-teal-400 border-teal-400/30",
};

interface SceneSelectorProps {
  types: SceneTypeInfo[];
  selectedIds: Set<string>;
  onToggle: (typeId: string) => void;
  params: Record<string, number>;
  onParamsChange: (params: Record<string, number>) => void;
  direction: string;
  onDirectionChange: (dir: string) => void;
}

export function SceneSelector({
  types,
  selectedIds,
  onToggle,
  params,
  onParamsChange,
  direction,
  onDirectionChange,
}: SceneSelectorProps) {
  // Group types by group field
  const groups = useMemo(() => {
    const map = new Map<string, SceneTypeInfo[]>();
    for (const t of types) {
      const g = t.group || "other";
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(t);
    }
    return Array.from(map.entries());
  }, [types]);

  // Collect param schemas for all selected types (deduped)
  const paramSchemas = useMemo(() => {
    const seen = new Set<string>();
    const schemas: typeof SCENE_PARAM_SCHEMAS[string] = [];
    for (const id of selectedIds) {
      const s = SCENE_PARAM_SCHEMAS[id];
      if (s) {
        for (const item of s) {
          if (!seen.has(item.key)) {
            seen.add(item.key);
            schemas.push(item);
          }
        }
      }
    }
    return schemas;
  }, [selectedIds]);

  // Direction selector: show if any selected type needs it
  const directionOptions = selectedIds.has("support_resistance")
    ? [
        { value: "support", label: "支撑位" },
        { value: "resistance", label: "阻力位" },
      ]
    : selectedIds.has("mean_reversion")
      ? [
          { value: "below", label: "低于均线" },
          { value: "above", label: "高于均线" },
          { value: "both", label: "双向偏离" },
        ]
      : null;

  return (
    <div className="flex flex-col gap-3">
      {/* Grouped checkboxes */}
      {groups.map(([groupName, items]) => (
        <div key={groupName} className="flex flex-col gap-1.5">
          <span className="text-[11px] text-slate-500 font-medium">{groupName}</span>
          <div className="flex flex-wrap gap-1.5">
            {items.map((t) => {
              const isActive = selectedIds.has(t.id);
              const colorClass = SUBTYPE_COLORS[t.id] ?? "bg-amber-400/15 text-amber-400 border-amber-400/30";
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => onToggle(t.id)}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors",
                    isActive
                      ? colorClass
                      : "border-slate-700/30 bg-white/[0.02] text-slate-500 hover:text-slate-400",
                  )}
                >
                  <span
                    className={cn(
                      "flex h-3.5 w-3.5 items-center justify-center rounded border text-[8px]",
                      isActive
                        ? "border-current"
                        : "border-slate-600",
                    )}
                  >
                    {isActive && "\u2713"}
                  </span>
                  {t.label}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {/* Direction selector */}
      {directionOptions && (
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">方向</span>
          <div className="flex gap-1">
            {directionOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onDirectionChange(opt.value)}
                className={cn(
                  "rounded px-2 py-0.5 text-[11px] transition-colors",
                  direction === opt.value
                    ? "bg-amber-400/20 text-amber-400"
                    : "bg-slate-800/30 text-slate-500 hover:text-slate-400",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Parameter sliders */}
      {paramSchemas.length > 0 && (
        <div className="flex flex-col gap-2 rounded-lg border border-slate-700/20 bg-white/[0.01] p-3">
          <span className="text-[11px] text-slate-500">场景参数</span>
          {paramSchemas.map((schema) => {
            const val = params[schema.key] ?? schema.defaultValue;
            return (
              <div key={schema.key} className="flex items-center gap-3">
                <span className="w-20 shrink-0 text-[11px] text-slate-400">
                  {schema.label}
                </span>
                <input
                  type="range"
                  min={schema.min}
                  max={schema.max}
                  step={schema.step}
                  value={val}
                  onChange={(e) =>
                    onParamsChange({ ...params, [schema.key]: Number(e.target.value) })
                  }
                  className="h-1 flex-1 cursor-pointer appearance-none rounded bg-slate-700 accent-amber-400"
                />
                <span className="w-10 text-right text-[11px] font-mono text-slate-300">
                  {val}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
