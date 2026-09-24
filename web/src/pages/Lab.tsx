import { useState, useCallback, useRef, useMemo, useEffect } from "react";
import {
  Play,
  Save,
  FlaskConical,
  ChevronDown,
  ChevronUp,
  RotateCcw,
  BarChart3,
  Crosshair,
  Settings2,
} from "lucide-react";
import { toast } from "sonner";
import { useLocation } from "react-router";

import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { KlineChart } from "@/components/charts/KlineChart";
import type { KlineChartHandle } from "@/components/charts/KlineChart";
import { ChartToolbar } from "@/components/charts/ChartToolbar";
import { Button as AntButton, Select as AntSelect, Skeleton } from "antd";

import {
  RuleConditionGroup,
  SaveStrategyDialog,
  BacktestModePanel,
  SceneModePanel,
} from "@/components/lab";
import type { BacktestModePanelHandle } from "@/components/lab/BacktestModePanel";

import { useValidateRules } from "@/hooks/useValidation";
import { useCreateStrategy, useStrategies } from "@/hooks/useStrategies";
import { useAvailableSources } from "@/hooks/useDatasets";
import { useChartIndicators } from "@/hooks/useChartIndicators";
import type { SubChartType } from "@/hooks/useChartIndicators";
import { SYMBOL_OPTIONS, TIMEFRAME_SELECT_OPTIONS, LEVERAGE_OPTIONS, DIRECTION_OPTIONS } from "@/lib/constants";
import type {
  RuleCondition,
  RuleValidateResponse,
  DNA,
  Strategy,
} from "@/types/api";
import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const QUICK_DATES = [
  { label: "近3月", days: 90 },
  { label: "近6月", days: 180 },
  { label: "近1年", days: 365 },
  { label: "今年", days: -1 },
  { label: "全部", days: 0 },
];

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

/** Format a Date as YYYY-MM-DD in local timezone (avoids toISOString UTC shift). */
function toLocalDateStr(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Clamp a YYYY-MM-DD string to a valid date (e.g. 04-31 -> 04-30). */
function clampDate(dateStr: string): string {
  if (!dateStr) return dateStr;
  const [y, m, d] = dateStr.split("-").map(Number);
  if (!y || !m || !d) return dateStr;
  const lastDay = new Date(y, m, 0).getDate();
  const clampedDay = Math.min(d, lastDay);
  const mm = String(m).padStart(2, "0");
  const dd = String(clampedDay).padStart(2, "0");
  return `${y}-${mm}-${dd}`;
}

function getDefaultDates(): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  start.setFullYear(start.getFullYear() - 1);
  return {
    start: toLocalDateStr(start),
    end: toLocalDateStr(end),
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Lab() {
  // -- Router state (DNA from Evolution) --
  const location = useLocation();
  const routeState = location.state as {
    dna?: DNA;
    symbol?: string;
    timeframe?: string;
    dataStart?: string;
    dataEnd?: string;
  } | null;

  // -- Lab mode --
  type LabMode = "hypothesis" | "backtest" | "scene";
  const [labMode, setLabMode] = useState<LabMode>(
    routeState?.dna ? "backtest" : "hypothesis"
  );

  // -- Backtest mode state (editable) --
  const [backtestDna, setBacktestDna] = useState<DNA | null>(routeState?.dna ?? null);
  const [backtestSymbol, setBacktestSymbol] = useState(routeState?.symbol ?? "BTCUSDT");
  const [backtestTimeframe, setBacktestTimeframe] = useState(routeState?.timeframe ?? "4h");
  const [backtestDateRange, setBacktestDateRange] = useState<{ start: string; end: string }>({
    start: routeState?.dataStart ?? "",
    end: routeState?.dataEnd ?? "",
  });
  const [btLeverage, setBtLeverage] = useState(1);
  const [btDirection, setBtDirection] = useState<"long" | "short" | "mixed">("long");
  const [btStopLoss, setBtStopLoss] = useState(0.05);
  const [btTakeProfit, setBtTakeProfit] = useState(0.1);
  const [btPositionSize, setBtPositionSize] = useState(0.3);
  const [btFee, setBtFee] = useState(0.001);
  const [btSlippage, setBtSlippage] = useState(0.0005);
  const [btInitCash, setBtInitCash] = useState(100000);
  const [btAdvancedOpen, setBtAdvancedOpen] = useState(false);
  const [btConfigCollapsed, setBtConfigCollapsed] = useState(!!routeState?.dna);
  const [selectedStrategyId, setSelectedStrategyId] = useState("");
  const backtestPanelRef = useRef<BacktestModePanelHandle>(null);

  // Clear route state on first load so refresh returns to hypothesis mode
  useEffect(() => {
    if (routeState?.dna) {
      window.history.replaceState({}, "");
    }
  }, []);

  // -- Builder state --
  const [pair, setPair] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("15m");
  const [dateRange, setDateRange] = useState(getDefaultDates);
  const [entryConditions, setEntryConditions] = useState<RuleCondition[]>([]);
  const [exitConditions, setExitConditions] = useState<RuleCondition[]>([]);
  const [builderCollapsed, setBuilderCollapsed] = useState(false);

  // -- Result state --
  const [ruleResult, setRuleResult] = useState<RuleValidateResponse | null>(null);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [hypoSubChartType] = useState<SubChartType>("volume");
  const [hypoFetchEnabled, setHypoFetchEnabled] = useState(false);

  // -- Refs --
  const chartRef = useRef<KlineChartHandle>(null);

  // -- Chart indicators hook (shared for hypothesis mode) --
  const {
    candleData,
    chartIndicators,
    chartBollData,
    volumeData,
    macdData: hypoMacdData,
    kdjData: hypoKdjData,
  } = useChartIndicators({
    symbol: pair,
    timeframe,
    dateRange,
    subChartType: hypoSubChartType,
    enabled: hypoFetchEnabled,
  });

  // -- Mutations --
  const validateMutation = useValidateRules();
  const createStrategyMutation = useCreateStrategy();

  // -- Available data sources --
  const { data: sourcesData } = useQuery(useAvailableSources());

  // -- Strategy library (for backtest strategy selector) --
  const { data: strategiesData } = useQuery(
    useStrategies({ sort_by: "created_at", sort_order: "desc", limit: 100 })
  );
  const allStrategies = strategiesData?.items ?? [];

  // When a strategy is selected from the library, populate backtest config
  const handleSelectStrategy = useCallback(
    (strategy: Strategy) => {
      if (strategy.dna) {
        setBacktestDna(strategy.dna);
        setBacktestSymbol(strategy.symbol);
        setBacktestTimeframe(strategy.timeframe);
        setBtLeverage(strategy.dna.risk_genes?.leverage ?? 1);
        setBtDirection(strategy.dna.risk_genes?.direction ?? "long");
        setBtStopLoss(strategy.dna.risk_genes?.stop_loss ?? 0.05);
        setBtTakeProfit(strategy.dna.risk_genes?.take_profit ?? 0.1);
        setBtPositionSize(strategy.dna.risk_genes?.position_size ?? 0.3);
        setSelectedStrategyId(strategy.strategy_id);
        setBtConfigCollapsed(false);
      }
    },
    [],
  );

  // Build dynamic symbol and timeframe options from available sources
  const dynamicSymbolOptions = useMemo(() => {
    if (!sourcesData?.sources?.length) return SYMBOL_OPTIONS;
    const seen = new Set<string>();
    const opts: { value: string; label: string }[] = [];
    for (const s of sourcesData.sources) {
      if (!seen.has(s.symbol)) {
        seen.add(s.symbol);
        opts.push({ value: s.symbol, label: s.symbol });
      }
    }
    return opts.length > 0 ? opts : SYMBOL_OPTIONS;
  }, [sourcesData]);

  const dynamicTimeframeOptions = useMemo(() => {
    if (!sourcesData?.sources?.length) return TIMEFRAME_SELECT_OPTIONS;
    const filtered = sourcesData.sources.filter((s) => s.symbol === pair);
    if (filtered.length === 0) return TIMEFRAME_SELECT_OPTIONS;
    const seen = new Set<string>();
    const opts: { value: string; label: string }[] = [];
    for (const s of filtered) {
      if (!seen.has(s.timeframe)) {
        seen.add(s.timeframe);
        opts.push({ value: s.timeframe, label: s.timeframe });
      }
    }
    return opts.length > 0 ? opts : TIMEFRAME_SELECT_OPTIONS;
  }, [sourcesData, pair]);

  // Auto-set default date range
  useEffect(() => {
    const match = sourcesData?.sources?.find(
      (s) => s.symbol === pair && s.timeframe === timeframe
    );
    if (match?.time_end) {
      const end = new Date(match.time_end);
      const start = new Date(end);
      start.setFullYear(start.getFullYear() - 1);
      const startDate = toLocalDateStr(start);
      const clampStart = match.time_start && startDate < match.time_start
        ? match.time_start
        : startDate;
      const newEnd = toLocalDateStr(end);
      setDateRange((prev) => {
        if (prev.start === clampStart && prev.end === newEnd) return prev;
        return { start: clampStart, end: newEnd };
      });
    }
  }, [pair, timeframe, sourcesData]);

  // -- Backtest quick date handler --
  const handleBtQuickDate = useCallback(
    (days: number) => {
      const match = sourcesData?.sources?.find(
        (s) => s.symbol === backtestSymbol && s.timeframe === backtestTimeframe
      );
      const endStr = match?.time_end ? match.time_end.slice(0, 10) : toLocalDateStr(new Date());
      const endDate = new Date(endStr);
      let startDate: Date;
      if (days === 0) {
        startDate = match?.time_start ? new Date(match.time_start) : new Date(endDate.getFullYear() - 1, 0, 1);
      } else if (days === -1) {
        startDate = new Date(endDate.getFullYear(), 0, 1);
      } else {
        startDate = new Date(endDate);
        startDate.setDate(startDate.getDate() - days);
      }
      const startStr = toLocalDateStr(startDate);
      setBacktestDateRange({ start: clampDate(startStr), end: endStr });
    },
    [sourcesData, backtestSymbol, backtestTimeframe],
  );

  // -- baseTimeframe = timeframe (data source period) --
  const baseTimeframe = timeframe;

  // -- Derived --
  const hasConditions = entryConditions.length > 0 && exitConditions.length > 0;
  const isLoading = validateMutation.isPending;
  const hasResult = ruleResult !== null;

  // Collect all referenced timeframes for the condition builders
  const availableTimeframes = useMemo(() => {
    const tfs = new Set<string>();
    tfs.add(baseTimeframe);
    for (const c of [...entryConditions, ...exitConditions]) {
      if (c.timeframe) tfs.add(c.timeframe);
    }
    // Also add common timeframes from dynamic options
    for (const o of dynamicTimeframeOptions) {
      tfs.add(o.value);
    }
    // Sort by duration (longest first)
    const ordered = ["3d", "1d", "4h", "1h", "15m", "5m", "1m"];
    return ordered.filter((tf) => tfs.has(tf));
  }, [baseTimeframe, entryConditions, exitConditions, dynamicTimeframeOptions]);

  // -- Handlers --
  const handleQuickDate = useCallback((days: number) => {
    const end = new Date();
    if (days === 0) {
      const match = sourcesData?.sources?.find(
        (s) => s.symbol === pair && s.timeframe === timeframe
      );
      const start = match?.time_start ?? "2024-01-01";
      setDateRange({ start, end: toLocalDateStr(end) });
      return;
    }
    const start = new Date();
    if (days === -1) {
      start.setMonth(0, 1);
      start.setHours(0, 0, 0, 0);
    } else {
      start.setDate(start.getDate() - days);
    }
    setDateRange({
      start: toLocalDateStr(start),
      end: toLocalDateStr(end),
    });
  }, [pair, timeframe, sourcesData]);

  const handleValidate = useCallback(async () => {
    if (entryConditions.length === 0) {
      toast.error("请至少添加一个入场条件");
      return;
    }
    if (exitConditions.length === 0) {
      toast.error("请至少添加一个出场条件");
      return;
    }

    // Validate dates before submitting
    if (!dateRange.start || !dateRange.end) {
      toast.error("请输入有效的日期范围");
      return;
    }
    if (dateRange.start > dateRange.end) {
      toast.error("开始日期不能晚于结束日期");
      return;
    }

    const safeStart = clampDate(dateRange.start);
    const safeEnd = clampDate(dateRange.end);

    try {
      const res = await validateMutation.mutateAsync({
        pair,
        timeframe,
        start: safeStart,
        end: safeEnd,
        entry_conditions: entryConditions,
        exit_conditions: exitConditions,
      });
      setRuleResult(res);
      setHypoFetchEnabled(true);
    } catch {
      // handled by mutation
    }
  }, [pair, timeframe, dateRange, entryConditions, exitConditions, validateMutation]);

  const handleClearAll = useCallback(() => {
    setEntryConditions([]);
    setExitConditions([]);
    setRuleResult(null);
    setHypoFetchEnabled(false);
  }, []);

  const handleSaveStrategy = useCallback(async (data: { name: string; description: string; tags: string }) => {
    if (!ruleResult) return;
    try {
      // Build a simple DNA from entry/exit conditions
      const entrySignals = entryConditions.map((c) => ({
        indicator: c.subject,
        params: {},
        role: "entry_trigger",
        field_name: null,
        condition: { type: c.action, threshold: c.target },
        timeframe: c.timeframe,
      }));
      const exitSignals = exitConditions.map((c) => ({
        indicator: c.subject,
        params: {},
        role: "exit_trigger",
        field_name: null,
        condition: { type: c.action, threshold: c.target },
        timeframe: c.timeframe,
      }));
      const generatedDna = {
        signal_genes: [...entrySignals, ...exitSignals],
        logic_genes: { entry_logic: "AND", exit_logic: "OR" },
        execution_genes: { timeframe, symbol: pair },
        risk_genes: { stop_loss: 0.05, take_profit: 0.1, position_size: 0.3, leverage: 1, direction: "long", sl_mode: "pct", atr_period: 14 },
      };
      await createStrategyMutation.mutateAsync({
        name: data.name,
        dna: generatedDna,
        symbol: pair,
        timeframe,
        source: "lab",
        tags: data.tags,
        notes: data.description,
      });
      toast.success("策略已保存");
      setSaveDialogOpen(false);
    } catch {
      // handled by mutation
    }
  }, [ruleResult, entryConditions, exitConditions, pair, timeframe, createStrategyMutation]);

  // -- Buy/Sell signals for chart --
  const chartSignals = useMemo(() => {
    if (!ruleResult) return [];
    const signals: Array<{ type: "buy" | "sell"; timestamp: string }> = [];
    for (const s of ruleResult.buy_signals) {
      signals.push({ type: "buy", timestamp: s.time });
    }
    for (const s of ruleResult.sell_signals) {
      signals.push({ type: "sell", timestamp: s.time });
    }
    return signals;
  }, [ruleResult]);

  // -- Collapsed summary --
  const collapsedSummary = useMemo(() => {
    const parts = [`${pair} ${timeframe}`];
    if (entryConditions.length > 0) {
      const entryStr = entryConditions
        .filter((c) => c.subject)
        .map((c) => `${c.timeframe}:${c.subject} ${c.action} ${c.target}`)
        .join(" + ");
      parts.push(`入场: ${entryStr}`);
    }
    if (exitConditions.length > 0) {
      const exitStr = exitConditions
        .filter((c) => c.subject)
        .map((c) => `${c.timeframe}:${c.subject} ${c.action} ${c.target}`)
        .join(" + ");
      parts.push(`出场: ${exitStr}`);
    }
    return parts.join(" / ");
  }, [pair, timeframe, entryConditions, exitConditions]);

  return (
    <PageTransition>
      <div className="flex flex-col gap-4">
        {/* Mode switcher */}
        <div className="flex items-center gap-1 rounded-lg border border-slate-700/30 bg-white/[0.02] p-1 w-fit">
          <button
            type="button"
            onClick={() => setLabMode("hypothesis")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              labMode === "hypothesis"
                ? "bg-amber-400/15 text-amber-400"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            <FlaskConical className="h-3.5 w-3.5" />
            假设验证
          </button>
          <button
            type="button"
            onClick={() => setLabMode("backtest")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              labMode === "backtest"
                ? "bg-amber-400/15 text-amber-400"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            <BarChart3 className="h-3.5 w-3.5" />
            策略回测
          </button>
          <button
            type="button"
            onClick={() => setLabMode("scene")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              labMode === "scene"
                ? "bg-amber-400/15 text-amber-400"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            <Crosshair className="h-3.5 w-3.5" />
            场景验证
          </button>
        </div>

        {/* Backtest mode */}
        {labMode === "backtest" && (
          <GlassCard className="p-5" hover={false}>
            <div className="flex flex-col gap-5">
              {/* Config section (collapsible) */}
              {btConfigCollapsed ? (
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3 text-xs text-slate-400">
                    <span className="font-medium text-slate-200">{backtestSymbol}</span>
                    <span>{backtestTimeframe.toUpperCase()}</span>
                    {backtestDna && (
                      <>
                        <span>{btLeverage}x</span>
                        <span>{DIRECTION_OPTIONS.find((o) => o.value === btDirection)?.label ?? "做多"}</span>
                      </>
                    )}
                  </div>
                  <AntButton type="text" size="small" onClick={() => setBtConfigCollapsed(false)}>
                    <ChevronDown className="h-3.5 w-3.5" />
                    配置
                  </AntButton>
                </div>
              ) : (
                <div className="flex flex-col gap-4">
                  {/* Strategy selector */}
                  <div>
                    <span className="mb-1.5 block text-xs font-medium text-text-muted">
                      策略选择
                    </span>
                    <AntSelect
                      size="small"
                      value={selectedStrategyId}
                      onChange={(v) => {
                        const s = allStrategies.find((st) => st.strategy_id === v);
                        if (s) handleSelectStrategy(s);
                      }}
                      placeholder={allStrategies.length === 0 ? "策略库为空，请先从进化中心保存策略" : "从策略库选择..."}
                      className="!w-full !text-xs"
                      aria-label="从策略库选择"
                      options={allStrategies.map((s) => ({
                        value: s.strategy_id,
                        label: `${s.name || s.strategy_id.slice(0, 8)} | ${s.symbol} ${s.timeframe} ${s.source === "evolution" ? "(进化)" : "(手动)"}`,
                      }))}
                    />
                  </div>

                  {/* Data source row */}
                  <div>
                    <span className="mb-1.5 block text-xs font-medium text-text-muted">
                      数据源
                    </span>
                    <div className="flex flex-wrap items-center gap-3">
                      <AntSelect
                        size="small"
                        value={backtestSymbol}
                        onChange={setBacktestSymbol}
                        className="!w-36 !text-xs"
                        aria-label="回测交易对"
                        options={[...dynamicSymbolOptions]}
                      />

                      <AntSelect
                        size="small"
                        value={backtestTimeframe}
                        onChange={setBacktestTimeframe}
                        className="!w-24 !text-xs"
                        aria-label="回测周期"
                        options={
                          (sourcesData?.sources?.filter((s) => s.symbol === backtestSymbol).length ?? 0) > 0
                            ? sourcesData!.sources
                                .filter((s) => s.symbol === backtestSymbol)
                                .reduce<{ value: string; label: string }[]>((acc, s) => {
                                  if (!acc.find((a) => a.value === s.timeframe)) {
                                    acc.push({ value: s.timeframe, label: s.timeframe });
                                  }
                                  return acc;
                                }, [])
                            : [...TIMEFRAME_SELECT_OPTIONS]
                        }
                      />

                      <input
                        type="date"
                        value={backtestDateRange.start}
                        onChange={(e) =>
                          setBacktestDateRange((prev) => ({ ...prev, start: e.target.value }))
                        }
                        className="h-8 w-28 cursor-pointer rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold [::-webkit-calendar-picker-indicator]:cursor-pointer"
                      />
                      <span className="text-xs text-text-muted">~</span>
                      <input
                        type="date"
                        value={backtestDateRange.end}
                        onChange={(e) =>
                          setBacktestDateRange((prev) => ({ ...prev, end: e.target.value }))
                        }
                        className="h-8 w-28 cursor-pointer rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold [::-webkit-calendar-picker-indicator]:cursor-pointer"
                      />

                      <div className="flex items-center gap-1">
                        {QUICK_DATES.map((qd) => (
                          <button
                            key={qd.label}
                            type="button"
                            onClick={() => handleBtQuickDate(qd.days)}
                            className="rounded px-2 py-1 text-[11px] text-text-muted transition-colors hover:bg-white/5 hover:text-text-secondary"
                          >
                            {qd.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Advanced parameters (collapsible) */}
                  <div>
                    <button
                      type="button"
                      className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary"
                      onClick={() => setBtAdvancedOpen(!btAdvancedOpen)}
                    >
                      <Settings2 className="h-3.5 w-3.5" />
                      高级参数
                      {btAdvancedOpen ? (
                        <ChevronUp className="h-3 w-3" />
                      ) : (
                        <ChevronDown className="h-3 w-3" />
                      )}
                    </button>
                    {btAdvancedOpen && (
                      <div className="mt-2 flex flex-wrap items-center gap-4">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">方向</span>
                          <AntSelect
                            size="small"
                            value={btDirection}
                            onChange={(v) => setBtDirection(v as "long" | "short" | "mixed")}
                            className="!w-20 !text-xs"
                            aria-label="回测方向"
                            options={DIRECTION_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">杠杆</span>
                          <AntSelect
                            size="small"
                            value={String(btLeverage)}
                            onChange={(v) => setBtLeverage(Number(v))}
                            className="!w-16 !text-xs"
                            aria-label="回测杠杆"
                            options={LEVERAGE_OPTIONS.map((o) => ({ value: String(o.value), label: o.label }))}
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">手续费</span>
                          <input
                            type="number"
                            step="0.0001"
                            min={0}
                            max={0.01}
                            value={btFee}
                            onChange={(e) => setBtFee(Number(e.target.value))}
                            className="h-7 w-20 rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold"
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">滑点</span>
                          <input
                            type="number"
                            step="0.0001"
                            min={0}
                            max={0.01}
                            value={btSlippage}
                            onChange={(e) => setBtSlippage(Number(e.target.value))}
                            className="h-7 w-20 rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold"
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">初始资金</span>
                          <input
                            type="number"
                            step={10000}
                            min={1000}
                            value={btInitCash}
                            onChange={(e) => setBtInitCash(Number(e.target.value))}
                            className="h-7 w-28 rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold"
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">止损</span>
                          <input
                            type="number"
                            step={0.01}
                            min={0}
                            max={0.5}
                            value={btStopLoss}
                            onChange={(e) => setBtStopLoss(Number(e.target.value))}
                            className="h-7 w-16 rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold"
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">止盈</span>
                          <input
                            type="number"
                            step={0.01}
                            min={0}
                            max={1}
                            value={btTakeProfit}
                            onChange={(e) => setBtTakeProfit(Number(e.target.value))}
                            className="h-7 w-16 rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold"
                          />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-text-muted">仓位</span>
                          <input
                            type="number"
                            step={0.1}
                            min={0.1}
                            max={1}
                            value={btPositionSize}
                            onChange={(e) => setBtPositionSize(Number(e.target.value))}
                            className="h-7 w-16 rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold"
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Action row */}
                  <div className="flex items-center gap-2">
                    {backtestDna && (
                      <AntButton type="text" size="small" className="!text-slate-500" onClick={() => setBtConfigCollapsed(true)}>
                        收起配置
                      </AntButton>
                    )}
                    <div className="flex-1" />
                    <AntButton
                      type="primary"
                      disabled={!backtestDna}
                      onClick={() => backtestPanelRef.current?.runBacktest()}
                    >
                      <Play className="h-4 w-4" />
                      运行回测
                    </AntButton>
                  </div>
                </div>
              )}

              {/* Backtest result panel */}
              {backtestDna ? (
                <BacktestModePanel
                  ref={backtestPanelRef}
                  dna={{
                    ...backtestDna,
                    risk_genes: {
                      ...backtestDna.risk_genes,
                      leverage: btLeverage,
                      direction: btDirection,
                      stop_loss: btStopLoss,
                      take_profit: btTakeProfit,
                      position_size: btPositionSize,
                    },
                  }}
                  symbol={backtestSymbol}
                  timeframe={backtestTimeframe}
                  dataStart={backtestDateRange.start || undefined}
                  dataEnd={backtestDateRange.end || undefined}
                  fee={btFee}
                  slippage={btSlippage}
                  initCash={btInitCash}
                />
              ) : (
                <div className="flex flex-col items-center gap-3 py-12 text-center">
                  <BarChart3 className="h-10 w-10 text-slate-700" />
                  <p className="text-xs text-slate-500">
                    从上方选择一个策略，或从进化中心跳转
                  </p>
                </div>
              )}
            </div>
          </GlassCard>
        )}

        {/* Scene mode */}
        {labMode === "scene" && (
          <GlassCard className="p-5" hover={false}>
            <SceneModePanel
              initialSymbol={pair}
              initialTimeframe={timeframe}
            />
          </GlassCard>
        )}

        {/* Hypothesis mode (original Lab UI) */}
        {labMode === "hypothesis" && (
          <>
        {/* Region 1: Condition Builder */}
        <GlassCard className="p-4" hover={false}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text-primary">规律构建器</h3>
            <button
              type="button"
              onClick={() => setBuilderCollapsed(!builderCollapsed)}
              className="flex items-center gap-1 text-xs text-text-muted transition-colors hover:text-text-secondary"
            >
              {builderCollapsed ? (
                <><ChevronUp className="h-3.5 w-3.5" />展开</>
              ) : (
                <><ChevronDown className="h-3.5 w-3.5" />收起</>
              )}
            </button>
          </div>

          {builderCollapsed ? (
            <div className="flex items-center justify-between py-1">
              <span className="truncate text-xs text-text-secondary">
                {collapsedSummary}
              </span>
              <button
                type="button"
                onClick={() => setBuilderCollapsed(false)}
                className="text-xs text-accent-gold"
              >
                展开
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {/* Data source row */}
              <div>
                <span className="mb-1.5 block text-xs font-medium text-text-muted">
                  数据源
                </span>
                <div className="flex flex-wrap items-center gap-3">
                  <AntSelect
                    size="small"
                    value={pair}
                    onChange={setPair}
                    className="!w-36 !text-xs"
                    aria-label="验证交易对"
                    options={[...dynamicSymbolOptions]}
                  />

                  <AntSelect
                    size="small"
                    value={timeframe}
                    onChange={setTimeframe}
                    className="!w-24 !text-xs"
                    aria-label="验证周期"
                    options={[...dynamicTimeframeOptions]}
                  />

                  <input
                    type="date"
                    value={dateRange.start}
                    onChange={(e) =>
                      setDateRange((prev) => ({ ...prev, start: e.target.value }))
                    }
                    className="h-8 w-28 cursor-pointer rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold [::-webkit-calendar-picker-indicator]:cursor-pointer"
                  />
                  <span className="text-xs text-text-muted">~</span>
                  <input
                    type="date"
                    value={dateRange.end}
                    onChange={(e) =>
                      setDateRange((prev) => ({ ...prev, end: e.target.value }))
                    }
                    className="h-8 w-28 cursor-pointer rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold [::-webkit-calendar-picker-indicator]:cursor-pointer"
                  />

                  <div className="flex items-center gap-1">
                    {QUICK_DATES.map((qd) => (
                      <button
                        key={qd.label}
                        type="button"
                        onClick={() => handleQuickDate(qd.days)}
                        className="rounded px-2 py-1 text-[11px] text-text-muted transition-colors hover:bg-white/5 hover:text-text-secondary"
                      >
                        {qd.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Entry rules */}
              <RuleConditionGroup
                title="入场规则"
                description="全部满足则买入"
                conditions={entryConditions}
                onConditionsChange={setEntryConditions}
                availableTimeframes={availableTimeframes}
              />

              {/* Exit rules */}
              <RuleConditionGroup
                title="出场规则"
                description="任一满足则卖出"
                conditions={exitConditions}
                onConditionsChange={setExitConditions}
                availableTimeframes={availableTimeframes}
              />

              {/* Action buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  type="button"
                  onClick={handleClearAll}
                  className="flex items-center gap-1 text-xs text-text-muted transition-colors hover:text-loss"
                >
                  <RotateCcw className="h-3 w-3" />
                  清空全部
                </button>

                <div className="flex items-center gap-2">
                  <AntButton
                    type="primary"
                    onClick={handleValidate}
                    disabled={isLoading || !hasConditions}
                  >
                    <Play className="h-3.5 w-3.5" />
                    {isLoading ? "验证中..." : "验证策略"}
                  </AntButton>

                  {hasResult && ruleResult && ruleResult.total_trades > 0 && (
                    <AntButton
                      onClick={() => setSaveDialogOpen(true)}
                      disabled={createStrategyMutation.isPending}
                    >
                      <Save className="h-3.5 w-3.5" />
                      保存为策略
                    </AntButton>
                  )}
                </div>
              </div>
            </div>
          )}
        </GlassCard>

        {/* Empty state / Loading / Results */}

        {!hasResult && !isLoading && (
          <GlassCard className="p-8" hover={false}>
            <div className="flex flex-col items-center gap-4">
              <FlaskConical className="h-16 w-16 text-text-muted/30" />
              <div className="flex flex-col items-center gap-1 text-center">
                <h3 className="text-base font-medium text-text-secondary">
                  配置入场和出场规则, 验证策略效果
                </h3>
                <p className="text-sm text-text-muted">
                  添加入场条件和出场条件, 然后点击"验证策略"
                </p>
              </div>
            </div>
          </GlassCard>
        )}

        {isLoading && (
          <>
            <GlassCard className="p-4" hover={false}>
              <div className="flex items-center gap-6">
                <Skeleton className="h-6 w-24" />
                <Skeleton className="h-6 w-32" />
                <Skeleton className="h-6 w-40" />
              </div>
            </GlassCard>
            <GlassCard className="p-4" hover={false}>
              <Skeleton className="h-[450px] w-full rounded-lg" />
            </GlassCard>
          </>
        )}

        {hasResult && !isLoading && ruleResult && (
          <>
            {/* Stats summary */}
            <GlassCard className="p-3" hover={false}>
              <div className="flex items-center gap-6 text-xs">
                <span className="text-text-secondary">
                  交易次数: <span className="font-medium text-text-primary">{ruleResult.total_trades}</span>
                </span>
                <span className="text-text-secondary">
                  胜率: <span className={`font-medium ${ruleResult.win_rate != null && ruleResult.win_rate >= 50 ? "text-profit" : "text-loss"}`}>
                    {ruleResult.win_rate != null ? `${ruleResult.win_rate}%` : "-"}
                  </span>
                </span>
                <span className="text-text-secondary">
                  盈利: <span className="font-medium text-profit">{ruleResult.win_trades}</span>
                </span>
                <span className="text-text-secondary">
                  亏损: <span className="font-medium text-loss">{ruleResult.loss_trades}</span>
                </span>
                <span className="text-text-secondary">
                  累计收益: <span className={`font-medium ${ruleResult.total_return_pct >= 0 ? "text-profit" : "text-loss"}`}>
                    {ruleResult.total_return_pct}%
                  </span>
                </span>
                <span className="text-text-secondary">
                  平均收益: <span className="font-medium text-text-primary">{ruleResult.avg_return_pct}%</span>
                </span>
              </div>
            </GlassCard>

            {/* No trades suggestion */}
            {ruleResult.total_trades === 0 && (
              <GlassCard className="p-4" hover={false}>
                <div className="flex flex-col items-center gap-3 text-center">
                  <p className="text-sm text-text-muted">未找到符合条件的交易信号</p>
                  <div className="flex items-center gap-2">
                    <AntButton size="small" onClick={handleClearAll}>
                      调整条件
                    </AntButton>
                    <AntButton size="small" onClick={() => handleQuickDate(0)}>
                      扩大时间范围
                    </AntButton>
                  </div>
                </div>
              </GlassCard>
            )}

            {/* KlineChart with buy/sell signals */}
            <GlassCard className="p-4" hover={false} id="lab-kline-section">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-3 text-xs text-text-muted">
                  <span className="font-medium text-text-secondary">
                    {pair} / {timeframe}
                  </span>
                  {ruleResult.buy_signals.length > 0 && (
                    <span className="text-profit">买入 {ruleResult.buy_signals.length}</span>
                  )}
                  {ruleResult.sell_signals.length > 0 && (
                    <span className="text-loss">卖出 {ruleResult.sell_signals.length}</span>
                  )}
                </div>
                <ChartToolbar
                  onZoomIn={() => chartRef.current?.zoomIn()}
                  onZoomOut={() => chartRef.current?.zoomOut()}
                  onReset={() => chartRef.current?.resetView()}
                  onFullscreen={() => {
                    const el = document.getElementById("lab-kline-section");
                    if (el && !document.fullscreenElement) {
                      el.requestFullscreen().catch(() => {});
                    } else {
                      document.exitFullscreen().catch(() => {});
                    }
                  }}
                />
              </div>
              <KlineChart
                ref={chartRef}
                data={candleData ?? []}
                indicators={chartIndicators}
                bollData={chartBollData}
                signals={chartSignals}
                height={650}
                volumeData={volumeData}
                subChartType={hypoSubChartType}
                macdData={hypoMacdData}
                kdjData={hypoKdjData}
              />
            </GlassCard>
          </>
        )}

        {/* Save Strategy Dialog */}
        <SaveStrategyDialog
          open={saveDialogOpen}
          onClose={() => setSaveDialogOpen(false)}
          onSave={handleSaveStrategy}
          pair={pair}
          timeframe={timeframe}
          matchRate={ruleResult?.win_rate ?? 0}
          totalCount={ruleResult?.total_trades ?? 0}
        />
          </>
        )}
      </div>
    </PageTransition>
  );
}
