import { useState, useCallback, useMemo, useEffect } from "react";
import { Play, ChevronDown, AlertTriangle, X, Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  SYMBOL_OPTIONS,
  TIMEFRAME_POOL_OPTIONS,
  TIMEFRAME_LABELS,
  TF_LAYER_ROLES,
  INDICATOR_GROUPS,
  INDICATOR_LABELS,
  LEVERAGE_OPTIONS,
  DIRECTION_OPTIONS,
  REQUIREMENTS_DEFAULTS,
  OBJECTIVE_OPTIONS,
  sortTimeframesLongestFirst,
} from "@/lib/constants";
import type { AvailableSource, RequirementsConfig } from "@/types/api";
import { getEvolutionTasks } from "@/services/evolution";
import type { Preset } from "@/components/evolution/QuickPresets";

interface AutoConfigFormProps {
  disabled: boolean;
  isPending: boolean;
  symbolOptions?: { value: string; label: string }[];
  availableSources?: AvailableSource[];
  initialPreset?: Preset | null;
  onPresetApplied?: () => void;
  onSubmit: (config: {
    symbol: string;
    timeframePool: string[];
    indicatorPool: string[];
    populationSize: number;
    maxGenerations: number;
    leverage: number;
    direction: "long" | "short" | "mixed";
    dataStart?: string;
    dataEnd?: string;
    requirements?: RequirementsConfig;
  }) => void;
}

export function AutoConfigForm({
  disabled,
  isPending,
  onSubmit,
  symbolOptions,
  availableSources,
  initialPreset,
  onPresetApplied,
}: AutoConfigFormProps) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframePool, setTimeframePool] = useState<string[]>(["4h"]);
  const [indicatorPool, setIndicatorPool] = useState<string[]>(
    INDICATOR_GROUPS.flatMap((g) => g.items)
  );
  const [advOpen, setAdvOpen] = useState(false);
  const [populationSize, setPopulationSize] = useState(15);
  const [maxGenerations, setMaxGenerations] = useState(200);
  const [leverage, setLeverage] = useState(1);
  const [direction, setDirection] = useState<"long" | "short" | "mixed">("long");
  const [dataStart, setDataStart] = useState("");
  const [dataEnd, setDataEnd] = useState("");
  const [requirements, setRequirements] = useState<RequirementsConfig>({
    objective: REQUIREMENTS_DEFAULTS.objective,
    min_annual_return: REQUIREMENTS_DEFAULTS.min_annual_return,
    max_drawdown: REQUIREMENTS_DEFAULTS.max_drawdown,
    min_win_rate: REQUIREMENTS_DEFAULTS.min_win_rate,
    min_total_trades: REQUIREMENTS_DEFAULTS.min_total_trades,
    min_profit_factor: REQUIREMENTS_DEFAULTS.min_profit_factor,
  });

  // Pre-fill date range from the most recent task that has dates
  useEffect(() => {
    getEvolutionTasks({ page: 1, page_size: 5 })
      .then((res) => {
        const last = res.items?.find(
          (t) => t.data_start && t.data_end
        );
        if (last?.data_start && last?.data_end) {
          setDataStart(last.data_start);
          setDataEnd(last.data_end);
        }
      })
      .catch(() => {});
  }, []);

  // Apply preset when provided
  useEffect(() => {
    if (!initialPreset) return;
    if (initialPreset.indicators) {
      setIndicatorPool(initialPreset.indicators);
    }
    if (initialPreset.timeframePool) {
      setTimeframePool(initialPreset.timeframePool);
    }
    if (initialPreset.requirements) {
      setRequirements(initialPreset.requirements);
    }
    onPresetApplied?.();
  }, [initialPreset, onPresetApplied]);

  // Filter timeframes that have data for the current symbol
  const availableTimeframes = useMemo(() => {
    if (!availableSources) return TIMEFRAME_POOL_OPTIONS;
    const tfs = availableSources
      .filter((s) => s.symbol === symbol)
      .map((s) => s.timeframe);
    return tfs.length > 0 ? tfs : TIMEFRAME_POOL_OPTIONS;
  }, [availableSources, symbol]);

  // Get primary timeframe data info
  const primarySourceInfo = useMemo(() => {
    if (!availableSources) return null;
    const primary = timeframePool[0] ?? "4h";
    return availableSources.find(
      (s) => s.symbol === symbol && s.timeframe === primary
    );
  }, [availableSources, symbol, timeframePool]);

  // Sorted pool (longest first) for display
  const sortedPool = useMemo(() => sortTimeframesLongestFirst(timeframePool), [timeframePool]);

  const addTimeframe = useCallback((tf: string) => {
    setTimeframePool((prev) => {
      if (prev.includes(tf)) return prev;
      if (prev.length >= 4) return prev;
      return sortTimeframesLongestFirst([...prev, tf]);
    });
  }, []);

  const removeTimeframe = useCallback((tf: string) => {
    setTimeframePool((prev) => prev.length > 1 ? prev.filter((t) => t !== tf) : prev);
  }, []);

  const toggleIndicator = useCallback((ind: string) => {
    setIndicatorPool((prev) =>
      prev.includes(ind) ? prev.filter((i) => i !== ind) : [...prev, ind]
    );
  }, []);

  const canSubmit = indicatorPool.length >= 2
    && requirements.max_drawdown > 0
    && !disabled;

  const handleSubmit = useCallback(() => {
    if (!canSubmit) return;
    onSubmit({
      symbol,
      timeframePool,
      indicatorPool,
      populationSize,
      maxGenerations,
      leverage,
      direction,
      dataStart: dataStart || undefined,
      dataEnd: dataEnd || undefined,
      requirements,
    });
  }, [
    canSubmit,
    onSubmit,
    symbol,
    timeframePool,
    indicatorPool,
    populationSize,
    maxGenerations,
    leverage,
    direction,
    dataStart,
    dataEnd,
    requirements,
  ]);

  return (
    <div className="flex flex-col gap-4">
      {/* Data source row */}
      <div className="flex items-center gap-3">
        <span className="w-14 shrink-0 text-xs text-slate-400">数据源</span>
        <Select value={symbol} onValueChange={setSymbol}>
          <SelectTrigger className="h-7 w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(symbolOptions ?? SYMBOL_OPTIONS).map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Data range info */}
      {primarySourceInfo && (
        <div className="flex items-center gap-2 rounded-lg border border-slate-700/20 bg-white/[0.01] px-3 py-2">
          <span className="text-[11px] text-slate-500">
            数据范围: {primarySourceInfo.time_start?.slice(0, 10) ?? "?"} ~ {primarySourceInfo.time_end?.slice(0, 10) ?? "?"}
          </span>
          {availableTimeframes.length === 0 && (
            <div className="flex items-center gap-1 text-[11px] text-amber-500">
              <AlertTriangle className="h-3 w-3" />
              无可用数据
            </div>
          )}
        </div>
      )}

      {/* Timeframe combination (ordered list) */}
      <div className="flex items-start gap-3">
        <span className="mt-1 w-14 shrink-0 text-xs text-slate-400">
          周期组合
        </span>
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-1.5">
            {sortedPool.map((tf, idx) => {
              const isLast = idx === sortedPool.length - 1;
              const role = TF_LAYER_ROLES[Math.min(idx, TF_LAYER_ROLES.length - 1)];
              return (
                <div
                  key={tf}
                  className={cn(
                    "flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px]",
                    isLast
                      ? "border-amber-400/40 bg-amber-400/15 text-amber-400"
                      : "border-slate-600/40 bg-slate-800/30 text-slate-300"
                  )}
                >
                  <span className="text-[9px] text-slate-500">{role}</span>
                  <span>{TIMEFRAME_LABELS[tf]}</span>
                  {sortedPool.length > 1 && (
                    <button
                      type="button"
                      className="ml-0.5 text-slate-600 hover:text-slate-400"
                      onClick={() => removeTimeframe(tf)}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              );
            })}
            {sortedPool.length < 4 && (
              <Select onValueChange={addTimeframe}>
                <SelectTrigger className="h-6 w-6 border-dashed border-slate-700/50 bg-transparent p-0 text-[10px] hover:border-slate-600">
                  <Plus className="h-3 w-3 text-slate-500" />
                </SelectTrigger>
                <SelectContent>
                  {TIMEFRAME_POOL_OPTIONS.filter(
                    (tf) => !timeframePool.includes(tf) && availableTimeframes.includes(tf)
                  ).map((tf) => (
                    <SelectItem key={tf} value={tf}>
                      {TIMEFRAME_LABELS[tf]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <span className="text-[11px] text-slate-500">
            {sortedPool.length >= 2
              ? `执行周期: ${TIMEFRAME_LABELS[sortedPool[sortedPool.length - 1]]} (最短) | ${sortedPool.length}层跨周期探索`
              : "添加多个周期开启跨周期策略探索"}
          </span>
        </div>
      </div>

      {/* Indicator pool */}
      <div className="flex items-start gap-3">
        <span className="mt-1 w-14 shrink-0 text-xs text-slate-400">
          指标池
        </span>
        <div className="flex flex-col gap-2">
          <span className="text-[11px] text-slate-500">
            已选 {indicatorPool.length}/21, 至少需要2个
          </span>
          {INDICATOR_GROUPS.map((group) => (
            <div key={group.label} className="flex flex-wrap items-center gap-1.5">
              <span className="w-10 text-[10px] text-slate-600">
                {group.label}
              </span>
              {group.items.map((ind) => {
                const selected = indicatorPool.includes(ind);
                return (
                  <Badge
                    key={ind}
                    variant="outline"
                    className={cn(
                      "cursor-pointer text-[11px] transition-colors",
                      selected
                        ? "border-amber-400/30 bg-amber-400/20 text-amber-400 hover:bg-amber-400/30"
                        : "border-slate-700/50 text-slate-400 hover:text-slate-300"
                    )}
                    onClick={() => toggleIndicator(ind)}
                  >
                    {INDICATOR_LABELS[ind] ?? ind}
                  </Badge>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Running requirements (directly visible, replaces optimize target) */}
      <div className="flex items-start gap-3">
        <span className="mt-1 w-14 shrink-0 text-xs text-slate-400">
          运行要求
        </span>
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-500">优化目标</span>
            <Select
              value={requirements.objective ?? "sharpe"}
              onValueChange={(v) =>
                setRequirements((prev) => ({ ...prev, objective: v }))
              }
            >
              <SelectTrigger className="h-7 w-28 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OBJECTIVE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-5 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">年化收益 %</label>
              <Input
                type="number"
                min={0}
                max={1000}
                step={1}
                value={requirements.min_annual_return * 100}
                onChange={(e) =>
                  setRequirements((prev) => ({
                    ...prev,
                    min_annual_return: Number(e.target.value) / 100 || 0,
                  }))
                }
                className="h-7 w-full text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">最大回撤 %</label>
              <Input
                type="number"
                min={0}
                max={100}
                step={1}
                value={requirements.max_drawdown * 100}
                onChange={(e) =>
                  setRequirements((prev) => ({
                    ...prev,
                    max_drawdown: Number(e.target.value) / 100 || 0,
                  }))
                }
                className="h-7 w-full text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">最低胜率 %</label>
              <Input
                type="number"
                min={0}
                max={100}
                step={1}
                value={requirements.min_win_rate * 100}
                onChange={(e) =>
                  setRequirements((prev) => ({
                    ...prev,
                    min_win_rate: Number(e.target.value) / 100 || 0,
                  }))
                }
                className="h-7 w-full text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">最少交易</label>
              <Input
                type="number"
                min={0}
                max={1000}
                step={1}
                value={requirements.min_total_trades}
                onChange={(e) =>
                  setRequirements((prev) => ({
                    ...prev,
                    min_total_trades: Number(e.target.value) || 0,
                  }))
                }
                className="h-7 w-full text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-slate-500">盈亏比</label>
              <Input
                type="number"
                min={0}
                max={10}
                step={0.1}
                value={requirements.min_profit_factor}
                onChange={(e) =>
                  setRequirements((prev) => ({
                    ...prev,
                    min_profit_factor: Number(e.target.value) || 0,
                  }))
                }
                className="h-7 w-full text-xs"
              />
            </div>
          </div>
          <span className="text-[10px] text-slate-600">
            至少配置收益和回撤两项, 满足所有要求的策略将被标记为达标
          </span>
        </div>
      </div>

      {/* Leverage & Direction (task-level constraints) */}
      <div className="flex items-center gap-4">
        <span className="w-14 shrink-0 text-xs text-slate-400">约束</span>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">杠杆</span>
          <Select value={String(leverage)} onValueChange={(v) => setLeverage(Number(v))}>
            <SelectTrigger className="h-7 w-16 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LEVERAGE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={String(opt.value)}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-500">方向</span>
          <Select value={direction} onValueChange={(v) => setDirection(v as "long" | "short" | "mixed")}>
            <SelectTrigger className="h-7 w-16 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DIRECTION_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {leverage > 1 && (
        <p className="text-[11px] text-amber-500/80">
          {leverage}x 杠杆: 每 8 小时收取 0.1% 资金费用, 保证金亏损超过 90% 触发爆仓
        </p>
      )}
      {direction === "mixed" && (
        <p className="text-[11px] text-purple-500/80">
          混合模式: 进化过程将自由探索做多和做空方向
        </p>
      )}

      {/* Data range (optional) */}
      <div className="flex items-center gap-3">
        <span className="w-14 shrink-0 text-xs text-slate-400">时间范围</span>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={dataStart}
            onChange={(e) => setDataStart(e.target.value)}
            className="h-7 rounded border border-slate-700/50 bg-transparent px-2 text-xs text-slate-300"
            placeholder="起始日期"
          />
          <span className="text-[11px] text-slate-600">~</span>
          <input
            type="date"
            value={dataEnd}
            onChange={(e) => setDataEnd(e.target.value)}
            className="h-7 rounded border border-slate-700/50 bg-transparent px-2 text-xs text-slate-300"
            placeholder="结束日期"
          />
        </div>
        {(dataStart || dataEnd) && (
          <span className="text-[10px] text-slate-600">留空则使用全部数据</span>
        )}
      </div>

      {/* Advanced params (collapsible) - only population size + max generations */}
      <div className="flex flex-col gap-2">
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-400"
          onClick={() => setAdvOpen((v) => !v)}
        >
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 transition-transform",
              advOpen && "rotate-180"
            )}
          />
          高级参数
        </button>
        {advOpen && (
          <div className="flex items-center gap-4 rounded-lg border border-slate-700/30 bg-white/[0.02] p-3">
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-slate-500">种群大小 (10-50)</label>
              <Input
                type="number"
                min={10}
                max={50}
                value={populationSize}
                onChange={(e) =>
                  setPopulationSize(Number(e.target.value) || 15)
                }
                className="h-7 w-24 text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[11px] text-slate-500">最大代数 (50-500)</label>
              <Input
                type="number"
                min={50}
                max={500}
                value={maxGenerations}
                onChange={(e) =>
                  setMaxGenerations(Number(e.target.value) || 200)
                }
                className="h-7 w-24 text-xs"
              />
            </div>
          </div>
        )}
      </div>

      {/* Submit */}
      <Button
        size="sm"
        className="w-full gap-1.5 bg-amber-400 text-black hover:bg-amber-400/90"
        disabled={!canSubmit || isPending}
        onClick={handleSubmit}
      >
        <Play className="h-3.5 w-3.5" />
        {isPending ? "创建中..." : "开始探索"}
      </Button>
    </div>
  );
}
