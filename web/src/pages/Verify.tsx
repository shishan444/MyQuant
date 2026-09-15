import { useState, useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import {
  Play, Loader2, Check, X as XIcon, Plus, Trash2, Clock,
  ShieldCheck, ChevronDown, ChevronUp,
  ArrowUpDown, History, Eye, Dna, TrendingUp,
} from "lucide-react";
import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  useStrategies,
  useVerifyStream,
  useVerifySessions,
  useSessionResults,
} from "@/hooks/useStrategies";
import { cn, getFitnessColor } from "@/lib/utils";
import type { VerifySummaryItem, VerifyPeriodSummary, Strategy, DNA, SignalGene, VerifySessionListResponse, VerifyHistoryResponse } from "@/types/api";

interface DateRange {
  start: string;
  end: string;
}

const DATE_PRESETS = [
  { label: "近3月", months: 3 },
  { label: "近6月", months: 6 },
  { label: "近1年", months: 12 },
] as const;

type SortKey = "score" | "qualified" | "return";
type FilterKey = "all" | "qualified" | "unqualified";

function formatReturn(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)}%`;
}

function monthsAgo(m: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - m);
  return d.toISOString().slice(0, 10);
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function describeCondition(cond: SignalGene["condition"], _indicator: string, _field?: string | null): string {
  const v = cond.value !== undefined ? cond.value.toFixed(cond.value < 1 ? 2 : 0) : "";
  const ref = cond.ref_indicator ? cond.ref_indicator : "";
  switch (cond.type) {
    case "cross_above": return ref ? `上穿${ref}` : `>${v}`;
    case "cross_below": return ref ? `下穿${ref}` : `<${v}`;
    case "gt": return `>${v}`;
    case "lt": return `<${v}`;
    case "ge": return `≥${v}`;
    case "le": return `≤${v}`;
    case "price_above": return "价格上方";
    case "price_below": return "价格下方";
    default: return "";
  }
}

function summarizeDna(dna: DNA): { entry: string; exit: string; risk: string } {
  const genes = dna.signal_genes ?? [];
  const logic = dna.logic_genes;
  const risk = dna.risk_genes;
  const layers = dna.layers;

  let entryParts: string[] = [];
  let exitParts: string[] = [];

  if (layers && layers.length > 0) {
    for (const layer of layers) {
      for (const g of layer.signal_genes) {
        const desc = `${g.indicator}(${Object.values(g.params).join(",")})${describeCondition(g.condition, g.indicator, g.field)}`;
        if (g.role.startsWith("entry")) entryParts.push(desc);
        else if (g.role.startsWith("exit")) exitParts.push(desc);
      }
    }
  } else {
    for (const g of genes) {
      const desc = `${g.indicator}(${Object.values(g.params).join(",")})${describeCondition(g.condition, g.indicator, g.field)}`;
      if (g.role.startsWith("entry")) entryParts.push(desc);
      else if (g.role.startsWith("exit")) exitParts.push(desc);
    }
  }

  const joiner = logic?.entry_logic === "OR" ? " OR " : " AND ";
  const exitJoiner = logic?.exit_logic === "OR" ? " OR " : " AND ";

  const riskDesc = [
    risk.leverage > 1 ? `杠杆${risk.leverage}x` : null,
    risk.direction === "long" ? "做多" : risk.direction === "short" ? "做空" : "双向",
    `止损${(risk.stop_loss * 100).toFixed(1)}%`,
    risk.take_profit ? `止盈${(risk.take_profit * 100).toFixed(1)}%` : null,
    `仓位${(risk.position_size * 100).toFixed(0)}%`,
  ].filter(Boolean).join(" · ");

  return {
    entry: entryParts.join(joiner) || "无入场条件",
    exit: exitParts.join(exitJoiner) || "无出场条件",
    risk: riskDesc,
  };
}

const STORAGE_KEY = "myquant_verify_config";

function loadSavedConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return null;
}

export function Verify() {
  const navigate = useNavigate();

  const saved = useMemo(() => loadSavedConfig(), []);
  const [dateRanges, setDateRanges] = useState<DateRange[]>(saved?.dateRanges ?? [{ start: "", end: "" }]);
  const [advancedParams, setAdvancedParams] = useState<{ init_cash: string; fee: string; slippage: string; leverage: string }>(saved?.advancedParams ?? {
    init_cash: "100000",
    fee: "0.001",
    slippage: "0.0005",
    leverage: "1",
  });
  const [configCollapsed, setConfigCollapsed] = useState(false);
  const [view, setView] = useState<"verify" | "history">("verify");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [filterKey, setFilterKey] = useState<FilterKey>("all");
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);
  const [expandedSessionId, setExpandedSessionId] = useState<string | null>(null);

  const stream = useVerifyStream();
  const { data: strategyData, isLoading: strategiesLoading } = useQuery(
    useStrategies({ sort_by: "created_at", sort_order: "desc", limit: 500 })
  );
  const { data: sessionsData, isLoading: sessionsLoading } = useQuery(useVerifySessions());
  const { data: sessionResults } = useQuery(useSessionResults(expandedSessionId));

  const strategies = strategyData?.items ?? [];
  const totalStrategies = strategyData?.total ?? 0;

  const strategyGroups = useMemo(() => {
    const map = new Map<string, { symbol: string; timeframe: string; count: number }>();
    for (const s of strategies) {
      const key = `${s.symbol}/${s.timeframe}`;
      const g = map.get(key);
      if (g) g.count++;
      else map.set(key, { symbol: s.symbol, timeframe: s.timeframe, count: 1 });
    }
    return Array.from(map.values());
  }, [strategies]);

  const handleDateChange = useCallback(
    (index: number, field: "start" | "end", value: string) => {
      setDateRanges((prev) => {
        const next = [...prev];
        next[index] = { ...next[index], [field]: value };
        return next;
      });
    },
    []
  );

  const applyPreset = useCallback((months: number) => {
    setDateRanges([{ start: monthsAgo(months), end: today() }]);
    setConfigCollapsed(false);
  }, []);

  const addDateRange = useCallback(() => {
    setDateRanges((prev) => (prev.length < 3 ? [...prev, { start: "", end: "" }] : prev));
  }, []);

  const removeDateRange = useCallback((index: number) => {
    setDateRanges((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const filledRanges = dateRanges.filter((r) => r.start && r.end);
  const canVerify = strategies.length > 0 && filledRanges.length > 0 && stream.status !== "running";

  const handleVerify = useCallback(() => {
    if (!canVerify) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ dateRanges, advancedParams }));
    stream.start({
      strategy_ids: strategies.map((s) => s.strategy_id),
      data_ranges: filledRanges,
      init_cash: parseFloat(advancedParams.init_cash) || 100000,
      fee: parseFloat(advancedParams.fee) || 0.001,
      slippage: parseFloat(advancedParams.slippage) || 0.0005,
      leverage: parseInt(advancedParams.leverage) || 1,
    });
    setConfigCollapsed(true);
  }, [canVerify, strategies, filledRanges, advancedParams, stream, dateRanges]);

  const handleCancel = useCallback(() => {
    stream.cancel();
    setConfigCollapsed(false);
  }, [stream]);

  const summaryItems: VerifySummaryItem[] = useMemo(() => {
    if (stream.status === "done" && stream.summary) {
      return stream.summary as VerifySummaryItem[];
    }
    return [];
  }, [stream.status, stream.summary]);

  const displayItems = useMemo(() => {
    let items = [...summaryItems];
    if (filterKey === "qualified") items = items.filter((s) => s.qualified_count === s.total_periods);
    else if (filterKey === "unqualified") items = items.filter((s) => s.qualified_count < s.total_periods);

    if (sortKey === "score") items.sort((a, b) => b.comprehensive_score - a.comprehensive_score);
    else if (sortKey === "qualified") items.sort((a, b) => b.qualified_count - a.qualified_count || b.comprehensive_score - a.comprehensive_score);
    else if (sortKey === "return") {
      items.sort((a, b) => {
        const aRet = a.per_period_metrics.reduce((s, p) => s + p.total_return, 0);
        const bRet = b.per_period_metrics.reduce((s, p) => s + p.total_return, 0);
        return bRet - aRet;
      });
    }
    return items;
  }, [summaryItems, filterKey, sortKey]);

  const stats = useMemo(() => {
    if (summaryItems.length === 0) return null;
    const qualified = summaryItems.filter((s) => s.qualified_count === s.total_periods).length;
    const avgScore = summaryItems.reduce((a, s) => a + s.comprehensive_score, 0) / summaryItems.length;
    const best = summaryItems.reduce((a, b) => (a.comprehensive_score > b.comprehensive_score ? a : b));
    return { total: summaryItems.length, qualified, avgScore, bestName: best.strategy_name, bestScore: best.comprehensive_score };
  }, [summaryItems]);

  const strategyMap = useMemo(() => {
    const map = new Map<string, Strategy>();
    for (const s of strategies) map.set(s.strategy_id, s);
    return map;
  }, [strategies]);

  if (!strategiesLoading && totalStrategies === 0) {
    return (
      <PageTransition>
        <div className="p-4">
          <EmptyState
            icon={ShieldCheck}
            title="策略库为空"
            description="策略验证需要至少一条策略。前往策略库或实验室创建策略。"
            actions={[
              { label: "前往策略库", onClick: () => navigate("/strategies"), variant: "outline" },
              { label: "前往实验室", onClick: () => navigate("/lab"), variant: "outline" },
            ]}
          />
        </div>
      </PageTransition>
    );
  }

  return (
    <PageTransition>
      <div className="p-4 space-y-3 max-w-[1400px] mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <ShieldCheck className="h-4 w-4 text-blue-400" />
            <h1 className="text-sm font-semibold text-text-primary">策略验证</h1>
            <span className="text-[11px] font-mono text-text-secondary">
              {totalStrategies} 条策略 · {strategyGroups.length} 品种
            </span>
          </div>
          <Button
            variant={view === "history" ? "default" : "ghost"}
            size="xs"
            onClick={() => setView(view === "history" ? "verify" : "history")}
          >
            <History className="w-3 h-3 mr-1" />
            {view === "history" ? "返回验证" : "历史记录"}
          </Button>
        </div>

        {view === "history" ? (
          <HistoryView
            sessionsData={sessionsData}
            sessionsLoading={sessionsLoading}
            expandedSessionId={expandedSessionId}
            setExpandedSessionId={setExpandedSessionId}
            sessionResults={sessionResults}
          />
        ) : (
          <>
            {/* Config Toolbar */}
            {configCollapsed && stream.status !== "running" ? (
              <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/40 border border-slate-700/30">
                <span className="text-xs text-text-secondary">
                  已验证 {filledRanges.length} 个区间 ({filledRanges.map((r) => `${r.start.slice(0, 10)}~${r.end.slice(0, 10)}`).join("、")}) · {advancedParams.init_cash} 资金
                </span>
                <Button variant="ghost" size="xs" onClick={() => { setConfigCollapsed(false); stream.reset(); }}>
                  重新验证
                </Button>
              </div>
            ) : (
              <div className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/30 space-y-2">
                {/* Line 1: Presets */}
                <div className="flex items-center justify-between">
                  <div className="flex gap-1">
                    {DATE_PRESETS.map((p) => (
                      <Button key={p.months} variant="ghost" size="xs" className="text-[10px] text-text-secondary" onClick={() => applyPreset(p.months)}>
                        {p.label}
                      </Button>
                    ))}
                  </div>
                  <Button variant="ghost" size="xs" onClick={addDateRange} className="text-[10px] text-text-secondary">
                    <Plus className="h-3 w-3 mr-0.5" /> 自定义区间
                  </Button>
                </div>

                {/* Date ranges */}
                <div className="space-y-1">
                  {dateRanges.map((range, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        type="date"
                        value={range.start}
                        onChange={(e) => handleDateChange(i, "start", e.target.value)}
                        className="bg-slate-800 border-slate-700/50 text-text-primary text-xs h-7 flex-1"
                      />
                      <span className="text-slate-600 text-xs">~</span>
                      <Input
                        type="date"
                        value={range.end}
                        onChange={(e) => handleDateChange(i, "end", e.target.value)}
                        className="bg-slate-800 border-slate-700/50 text-text-primary text-xs h-7 flex-1"
                      />
                      {dateRanges.length > 1 && (
                        <Button variant="ghost" size="icon-xs" onClick={() => removeDateRange(i)} className="text-slate-500 hover:text-red-400">
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>

                {/* Line 2: Params + Action */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">资金</span>
                      <Input
                        type="number"
                        step="10000"
                        min="1000"
                        value={advancedParams.init_cash}
                        onChange={(e) => setAdvancedParams((p) => ({ ...p, init_cash: e.target.value }))}
                        className="text-[11px] h-6 w-24 bg-slate-800 border-slate-700/50 text-text-primary"
                      />
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">杠杆</span>
                      <Input
                        type="number"
                        step="1"
                        min="1"
                        max="10"
                        value={advancedParams.leverage}
                        onChange={(e) => setAdvancedParams((p) => ({ ...p, leverage: e.target.value }))}
                        className="text-[11px] h-6 w-12 bg-slate-800 border-slate-700/50 text-text-primary"
                      />
                      <span className="text-[10px] text-text-secondary">x</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">费率</span>
                      <Input
                        type="number"
                        step="0.0001"
                        min="0"
                        max="0.01"
                        value={advancedParams.fee}
                        onChange={(e) => setAdvancedParams((p) => ({ ...p, fee: e.target.value }))}
                        className="text-[11px] h-6 w-20 bg-slate-800 border-slate-700/50 text-text-primary"
                      />
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">滑点</span>
                      <Input
                        type="number"
                        step="0.0001"
                        min="0"
                        max="0.01"
                        value={advancedParams.slippage}
                        onChange={(e) => setAdvancedParams((p) => ({ ...p, slippage: e.target.value }))}
                        className="text-[11px] h-6 w-20 bg-slate-800 border-slate-700/50 text-text-primary"
                      />
                    </div>
                  </div>
                  <Button size="xs" disabled={!canVerify} onClick={handleVerify}>
                    <Play className="w-3 h-3 mr-1" />
                    开始验证 ({strategies.length})
                  </Button>
                </div>
              </div>
            )}

            {/* Progress Panel */}
            {stream.status === "running" && (
              <div className="p-4 rounded-lg bg-slate-800/40 border border-slate-700/30 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
                    <span className="text-sm font-medium text-text-primary">验证中...</span>
                  </div>
                  <span className="text-xs font-mono text-text-secondary">
                    {stream.progress.current}/{stream.progress.total} 步骤
                  </span>
                </div>
                <div className="w-full h-1.5 bg-slate-700/50 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-400 rounded-full transition-all duration-300"
                    style={{ width: `${stream.progress.total > 0 ? (stream.progress.current / stream.progress.total) * 100 : 0}%` }}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-text-secondary">
                    正在处理 {stream.currentGroup} · {stream.rangeLabel}
                  </span>
                  <Button variant="ghost" size="xs" onClick={handleCancel} className="text-red-400/80 hover:text-red-400">
                    取消验证
                  </Button>
                </div>
              </div>
            )}

            {/* Error state */}
            {stream.status === "error" && (
              <div className="p-3 rounded-lg bg-slate-800/40 border border-red-900/30 flex items-center gap-2">
                <XIcon className="w-4 h-4 text-red-400" />
                <span className="text-xs text-red-400">{stream.error}</span>
                <Button variant="ghost" size="xs" onClick={() => { stream.reset(); handleVerify(); }} className="ml-auto">
                  重试
                </Button>
              </div>
            )}

            {/* Results area */}
            {stream.status === "done" && stats && (
              <>
                {/* Stats strip */}
                <div className="flex items-center gap-4 px-1">
                  <Stat label="达标" value={`${stats.qualified}/${stats.total}`} trend={stats.qualified > 0 ? "up" : "neutral"} />
                  <Stat label="均分" value={stats.avgScore.toFixed(2)} trend={stats.avgScore > 0.5 ? "up" : stats.avgScore > 0.2 ? "neutral" : "down"} />
                  <Stat label="最佳" value={stats.bestName ?? "-"} trend="up" />
                </div>

                {/* Filter & Sort */}
                <div className="flex items-center justify-between">
                  <div className="flex gap-1">
                    {(["all", "qualified", "unqualified"] as FilterKey[]).map((k) => (
                      <Button
                        key={k}
                        variant={filterKey === k ? "default" : "ghost"}
                        size="xs"
                        className="text-[10px]"
                        onClick={() => setFilterKey(k)}
                      >
                        {k === "all" ? "全部" : k === "qualified" ? "达标" : "未达标"}
                      </Button>
                    ))}
                  </div>
                  <div className="flex items-center gap-1">
                    <ArrowUpDown className="w-3 h-3 text-text-secondary" />
                    {(["score", "qualified", "return"] as SortKey[]).map((k) => (
                      <Button
                        key={k}
                        variant={sortKey === k ? "default" : "ghost"}
                        size="xs"
                        className="text-[10px]"
                        onClick={() => setSortKey(k)}
                      >
                        {k === "score" ? "评分" : k === "qualified" ? "达标率" : "收益"}
                      </Button>
                    ))}
                  </div>
                </div>

                {/* Strategy Cards Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
                  {displayItems.map((item, idx) => (
                    <StrategyCard
                      key={item.strategy_id}
                      rank={idx + 1}
                      item={item}
                      strategy={strategyMap.get(item.strategy_id)}
                      expanded={expandedCardId === item.strategy_id}
                      onToggleExpand={() => setExpandedCardId(expandedCardId === item.strategy_id ? null : item.strategy_id)}
                      onTrade={() => {
                        const s = strategyMap.get(item.strategy_id);
                        if (!s?.dna) return;
                        navigate("/trading", { state: { dna: s.dna, symbol: s.symbol, timeframe: s.timeframe, strategyName: s.name } });
                      }}
                      onBacktest={() => {
                        const s = strategyMap.get(item.strategy_id);
                        if (!s?.dna) return;
                        navigate("/lab", { state: { dna: s.dna, symbol: s.symbol, timeframe: s.timeframe } });
                      }}
                      onEvolve={() => {
                        const s = strategyMap.get(item.strategy_id);
                        if (!s?.dna) return;
                        navigate("/evolution", { state: { seedDna: s.dna, symbol: s.symbol, timeframe: s.timeframe } });
                      }}
                    />
                  ))}
                </div>

                {displayItems.length === 0 && summaryItems.length > 0 && (
                  <div className="text-center py-8 text-xs text-text-secondary">
                    无匹配的策略。尝试调整筛选条件。
                  </div>
                )}
              </>
            )}

            {/* Initial empty guidance */}
            {stream.status === "idle" && (
              <div className="py-12 text-center">
                <ShieldCheck className="w-8 h-8 text-slate-600 mx-auto mb-3" />
                <p className="text-sm text-text-secondary">配置时间区间后开始验证</p>
                <p className="text-xs text-slate-500 mt-1">将验证全部 {strategies.length} 条策略的跨区间表现</p>
              </div>
            )}
          </>
        )}
      </div>
    </PageTransition>
  );
}

/* ── Sub-components ── */

function Stat({ label, value, trend }: { label: string; value: string; trend: "up" | "down" | "neutral" }) {
  const color = trend === "up" ? "text-emerald-400" : trend === "down" ? "text-red-400" : "text-text-primary";
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-[10px] text-text-secondary">{label}</span>
      <span className={cn("text-sm font-semibold font-mono", color)}>{value}</span>
    </div>
  );
}

function StrategyCard({
  rank,
  item,
  strategy,
  expanded,
  onToggleExpand,
  onTrade,
  onBacktest,
  onEvolve,
}: {
  rank: number;
  item: VerifySummaryItem;
  strategy?: Strategy;
  expanded: boolean;
  onToggleExpand: () => void;
  onTrade: () => void;
  onBacktest: () => void;
  onEvolve: () => void;
}) {
  const allQualified = item.qualified_count === item.total_periods;
  const hasDna = !!strategy?.dna;

  return (
    <GlassCard hover={false} className="p-3 space-y-2">
      {/* Row 1: Header — clickable for expand */}
      <button className="w-full text-left" onClick={onToggleExpand}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className={cn(
              "text-xs font-mono font-bold shrink-0",
              rank <= 3 ? "text-amber-400" : "text-text-secondary"
            )}>
              #{rank}
            </span>
            <span className="text-xs text-text-primary truncate" title={item.strategy_name}>
              {item.strategy_name}
            </span>
            {strategy && (
              <Badge variant="ghost" className="h-4 text-[9px] px-1 shrink-0">
                {strategy.source === "evolution" ? "进化" : strategy.source === "lab" ? "实验室" : strategy.source ?? ""}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {allQualified && (() => {
              const star = item.avg_fitness >= 6.0 ? 5 : item.avg_fitness >= 4.5 ? 4 : item.avg_fitness >= 3.0 ? 3 : item.avg_fitness >= 2.0 ? 2 : 1;
              return (
                <span className={cn(
                  "text-[10px] flex items-center",
                  star === 5 ? "text-amber-400" : star >= 3 ? "text-blue-400" : "text-slate-400"
                )}>
                  {Array.from({ length: 5 }, (_, i) => (
                    <span key={i} className={i < star ? "" : "opacity-30"}>★</span>
                  ))}
                </span>
              );
            })()}
            <span className={cn("text-sm font-bold font-mono", getFitnessColor(item.comprehensive_score))}>
              {item.comprehensive_score.toFixed(2)}
            </span>
            <span className={cn("text-[10px] font-mono", allQualified ? "text-emerald-400" : "text-text-secondary")}>
              {item.qualified_count}/{item.total_periods}
            </span>
            {expanded ? <ChevronUp className="w-3 h-3 text-text-secondary" /> : <ChevronDown className="w-3 h-3 text-text-secondary" />}
          </div>
        </div>
      </button>

      {/* Row 2: Period bars */}
      <div className="flex gap-1">
        {item.per_period_metrics.map((pm, pi) => (
          <PeriodBar key={pi} pm={pm} />
        ))}
      </div>

      {/* Row 3: Aggregate metrics */}
      {strategy && (
        <div className="flex items-center gap-3 text-[10px] text-text-secondary">
          <span>{strategy.symbol}/{strategy.timeframe}</span>
          <AggregateMetrics periods={item.per_period_metrics} />
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-slate-700/30 pt-2 space-y-2">
          {/* DNA summary */}
          {strategy?.dna && (
            <div className="text-[10px] space-y-0.5">
              <div className="text-text-secondary font-medium">策略基因</div>
              {(() => {
                const dna = summarizeDna(strategy.dna);
                return (
                  <div className="space-y-0.5 text-text-primary">
                    <div>入场: {dna.entry}</div>
                    <div>出场: {dna.exit}</div>
                    <div>风控: {dna.risk}</div>
                  </div>
                );
              })()}
            </div>
          )}

          {/* Per-period detail table */}
          <div className="text-[10px]">
            <div className="text-text-secondary font-medium mb-1">各区间详情</div>
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-700/30 text-text-secondary">
                  <th className="py-0.5 text-left font-medium">区间</th>
                  <th className="py-0.5 text-center font-medium">收益</th>
                  <th className="py-0.5 text-center font-medium">夏普</th>
                  <th className="py-0.5 text-center font-medium">回撤</th>
                  <th className="py-0.5 text-center font-medium">达标</th>
                </tr>
              </thead>
              <tbody>
                {item.per_period_metrics.map((pm, i) => (
                  <tr key={i} className="border-b border-slate-700/10">
                    <td className="py-0.5 text-text-secondary font-mono">{pm.data_start.slice(0, 10)}~{pm.data_end.slice(0, 10)}</td>
                    <td className={cn("py-0.5 text-center font-mono", pm.total_return >= 0 ? "text-emerald-400" : "text-red-400")}>
                      {formatReturn(pm.total_return)}
                    </td>
                    <td className="py-0.5 text-center font-mono text-text-primary">{pm.sharpe_ratio.toFixed(2)}</td>
                    <td className="py-0.5 text-center font-mono text-text-primary">{(pm.max_drawdown * 100).toFixed(1)}%</td>
                    <td className="py-0.5 text-center">
                      {pm.qualified ? <Check className="w-2.5 h-2.5 text-emerald-400 inline" /> : <XIcon className="w-2.5 h-2.5 text-red-400/60 inline" />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Expanded action buttons */}
          <div className="flex gap-2 pt-1">
            <Button size="xs" onClick={onTrade} disabled={!hasDna}
              className={cn(allQualified ? "bg-emerald-600 hover:bg-emerald-700" : "")}>
              <TrendingUp className="w-3 h-3 mr-1" /> 开始交易
            </Button>
            <Button variant="outline" size="xs" onClick={onBacktest} disabled={!hasDna}>
              <Eye className="w-3 h-3 mr-1" /> 查看回测
            </Button>
            <Button variant="outline" size="xs" onClick={onEvolve} disabled={!hasDna}>
              <Dna className="w-3 h-3 mr-1" /> 继续进化
            </Button>
          </div>
        </div>
      )}

      {/* Collapsed action buttons */}
      {!expanded && (
        <div className="flex items-center gap-2 pt-1 border-t border-slate-700/20">
          {allQualified ? (
            <Button size="xs" onClick={onTrade} disabled={!hasDna}
              className="bg-emerald-600 hover:bg-emerald-700 text-[10px]">
              <Play className="w-3 h-3 mr-0.5" /> 开始交易
            </Button>
          ) : (
            <Button size="xs" onClick={onEvolve} disabled={!hasDna}
              className="bg-blue-600 hover:bg-blue-700 text-[10px]">
              <Dna className="w-3 h-3 mr-0.5" /> 继续优化
            </Button>
          )}
          <Button variant="ghost" size="icon-xs" onClick={onBacktest} disabled={!hasDna} title="查看回测">
            <Eye className="w-3 h-3 text-text-secondary" />
          </Button>
          {allQualified ? (
            <Button variant="ghost" size="icon-xs" onClick={onEvolve} disabled={!hasDna} title="继续优化">
              <Dna className="w-3 h-3 text-text-secondary" />
            </Button>
          ) : (
            <Button variant="ghost" size="icon-xs" onClick={onTrade} disabled={!hasDna} title="模拟交易">
              <TrendingUp className="w-3 h-3 text-text-secondary" />
            </Button>
          )}
        </div>
      )}
    </GlassCard>
  );
}

function PeriodBar({ pm }: { pm: VerifyPeriodSummary }) {
  const ret = pm.total_return;
  const isPositive = ret >= 0;
  const barWidth = Math.min(Math.abs(ret) * 200, 100);

  return (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-1 mb-0.5">
        <div className="flex-1 h-1 bg-slate-700/40 rounded-full overflow-hidden">
          <div
            className={cn("h-full rounded-full", isPositive ? "bg-emerald-400/70" : "bg-red-400/70")}
            style={{ width: `${barWidth}%` }}
          />
        </div>
      </div>
      <div className="flex items-center justify-between">
        <span className={cn("text-[10px] font-mono", isPositive ? "text-emerald-400" : "text-red-400")}>
          {formatReturn(ret)}
        </span>
        {pm.qualified ? (
          <Check className="w-2.5 h-2.5 text-emerald-400" />
        ) : (
          <XIcon className="w-2.5 h-2.5 text-red-400/50" />
        )}
      </div>
    </div>
  );
}

function AggregateMetrics({ periods }: { periods: VerifyPeriodSummary[] }) {
  if (periods.length === 0) return null;
  const avgReturn = periods.reduce((s, p) => s + p.total_return, 0) / periods.length;
  const avgSharpe = periods.reduce((s, p) => s + p.sharpe_ratio, 0) / periods.length;
  const maxDD = Math.max(...periods.map((p) => p.max_drawdown));

  return (
    <>
      <span>年化 {formatReturn(avgReturn)}</span>
      <span>夏普 {avgSharpe.toFixed(2)}</span>
      <span>回撤 {(maxDD * 100).toFixed(1)}%</span>
    </>
  );
}

/* ── History View ── */

function HistoryView({
  sessionsData,
  sessionsLoading,
  expandedSessionId,
  setExpandedSessionId,
  sessionResults,
}: {
  sessionsData: VerifySessionListResponse | undefined;
  sessionsLoading: boolean;
  expandedSessionId: string | null;
  setExpandedSessionId: (id: string | null) => void;
  sessionResults: VerifyHistoryResponse | undefined;
}) {
  if (sessionsLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-12 bg-slate-800/30 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (!sessionsData || sessionsData.items.length === 0) {
    return (
      <EmptyState
        icon={Clock}
        title="暂无验证记录"
        description="完成一次策略验证后，结果将保存在这里。"
      />
    );
  }

  return (
    <div className="space-y-2">
      {sessionsData.items.map((session) => {
        const isExpanded = expandedSessionId === session.session_id;
        const ranges: Array<{ start: string; end: string }> = (() => { try { return JSON.parse(session.data_ranges); } catch { return []; } })();
        const strategyCount = (() => { try { return JSON.parse(session.strategy_ids).length; } catch { return 0; } })();

        return (
          <GlassCard key={session.session_id} hover={false}>
            <button
              className="w-full p-3 flex items-center justify-between text-left"
              onClick={() => setExpandedSessionId(isExpanded ? null : session.session_id)}
            >
              <div className="flex items-center gap-2.5">
                <Clock className="h-3 w-3 text-text-secondary" />
                <span className="text-xs text-text-primary font-mono">
                  {session.created_at.slice(0, 16).replace("T", " ")}
                </span>
                <Badge variant="outline" className={cn("h-4 text-[9px]",
                  session.status === "completed" ? "text-emerald-400 border-emerald-400/30" :
                  session.status === "failed" ? "text-red-400 border-red-400/30" :
                  "text-yellow-400 border-yellow-400/30"
                )}>
                  {session.status === "completed" ? "完成" : session.status === "failed" ? "失败" : "运行中"}
                </Badge>
                <span className="text-[11px] text-text-secondary">{strategyCount} 条策略</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-slate-500">
                  {ranges.map((r) => `${r.start.slice(0, 10)}~${r.end.slice(0, 10)}`).join("、")}
                </span>
                {isExpanded ? <ChevronUp className="h-3.5 w-3.5 text-text-secondary" /> : <ChevronDown className="h-3.5 w-3.5 text-text-secondary" />}
              </div>
            </button>

            {session.status === "failed" && session.error_message && (
              <div className="px-3 pb-2">
                <p className="text-[10px] text-red-400/80">{session.error_message}</p>
              </div>
            )}

            {isExpanded && (
              <div className="px-3 pb-3 border-t border-slate-700/30 pt-2 overflow-x-auto">
                {sessionResults ? (
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-slate-700/40">
                        <th className="py-1.5 text-left text-text-secondary font-medium">策略名</th>
                        <th className="py-1.5 text-left text-text-secondary font-medium">品种/周期</th>
                        <th className="py-1.5 text-center text-text-secondary font-medium">年化</th>
                        <th className="py-1.5 text-center text-text-secondary font-medium">Fitness</th>
                        <th className="py-1.5 text-center text-text-secondary font-medium">达标</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sessionResults.items.map((item) => (
                        <tr key={item.result_id} className="border-b border-slate-700/10">
                          <td className="py-1.5 text-text-primary truncate max-w-[120px]">{item.strategy_name ?? item.strategy_id}</td>
                          <td className="py-1.5 text-text-secondary font-mono text-[10px]">{item.symbol}/{item.timeframe}</td>
                          <td className={cn("py-1.5 text-center font-mono tabular-nums", item.total_return > 0 ? "text-emerald-400" : "text-red-400")}>
                            {formatReturn(item.total_return)}
                          </td>
                          <td className={cn("py-1.5 text-center font-mono tabular-nums font-medium", getFitnessColor(item.fitness))}>
                            {item.fitness.toFixed(2)}
                          </td>
                          <td className="py-1.5 text-center">
                            {item.qualified ? <Check className="w-3 h-3 text-emerald-400 inline" /> : <XIcon className="w-3 h-3 text-red-400/60 inline" />}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="py-4 text-center text-[11px] text-text-secondary">
                    <Loader2 className="w-3 h-3 animate-spin inline mr-1" /> 加载中...
                  </div>
                )}
              </div>
            )}
          </GlassCard>
        );
      })}
    </div>
  );
}
