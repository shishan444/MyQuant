/**
 * 策略验证页（路由 /verify）。多策略×多区间验证：配置工具栏
 * （预设/日期区间）→ 进度/结果（策略卡网格：区间条+聚合指标+
 * 展开明细）+历史会话视图（三门槛点行/星级）。消费方：App 路由
 * （页面入口）。
 */
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import {
  Play, Check, X as XIcon, Plus, Trash2, Clock,
  ShieldCheck, ChevronDown, ChevronUp,
  ArrowUpDown, History, Eye, Dna, TrendingUp,
} from "lucide-react";
import { LoadingOutlined } from "@ant-design/icons";
import { Button as AntButton, Input, InputNumber, Progress, Tag, Tooltip } from "antd";
import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { EmptyState } from "@/components/EmptyState";
import {
  FilterBar,
  RecordDetail,
  RecordsTable,
  useInvalidateRecords,
} from "@/components/validationRecords/RecordBrowser";
import type { ValidationRecordRow as ValidationRecordRowLite } from "@/services/validationRecords";
import {
  useStrategies,
  useVerifyStream,
  useVerifySessions,
  useSessionResults,
} from "@/hooks/useStrategies";
import { cn, getFitnessColor, monthsAgo, todayISO } from "@/lib/utils";
import type { VerifySummaryItem, VerifyPeriodSummary, Strategy, DNA, SignalGene, VerifySessionListResponse, VerifyHistoryResponse } from "@/types/api";
import { ThresholdDots, VerifyStar, ThresholdsSummary, type VerifyHistoryRow } from "@/components/verify/VerifyThresholds";
import { formatDrawdown } from "@/components/verify/thresholdsUtils";

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

function describeCondition(cond: SignalGene["condition"], _indicator: string, _field?: string | null): string {
  const v = cond.threshold !== undefined ? cond.threshold.toFixed(cond.threshold < 1 ? 2 : 0) : "";
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
  // fe-validation-records-page：浏览骨架态+发起器折叠态
  const [filters, setFilters] = useState<{ idType: string; id?: string; level?: string; verdict?: string }>({ idType: "hypothesis_id" });
  const [selectedRecord, setSelectedRecord] = useState<ValidationRecordRowLite | undefined>(undefined);
  const [launcherOpen, setLauncherOpen] = useState(false);
  const invalidateRecords = useInvalidateRecords();
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [filterKey, setFilterKey] = useState<FilterKey>("all");
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null);
  const [expandedSessionId, setExpandedSessionId] = useState<string | null>(null);

  const stream = useVerifyStream();
  // 同页闭环（V-I·Q3 修复）：失效时机=发起过的验证流离开 running
  // （done/error——记录落库时点），非发起点击时——点击时 refetch 拉不到
  // 未落库的新记录；重试路径（同走 start→running→done）同理受益
  const prevStreamStatusRef = useRef(stream.status);
  useEffect(() => {
    const was = prevStreamStatusRef.current;
    prevStreamStatusRef.current = stream.status;
    if (was === "running" && stream.status !== "running") {
      invalidateRecords();
    }
  }, [stream.status, invalidateRecords]);
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
    setDateRanges([{ start: monthsAgo(months), end: todayISO() }]);
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
    // 同页闭环（fe-validation-records-page V-I）：失效时机在 stream 完成态
    // effect（Q3 修复——见 stream.status 监听），此处不再点击时失效
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
        {/* 头部（fe-validation-records-page：浏览为主水体） */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <ShieldCheck className="h-4 w-4 text-blue-400" />
            <h1 className="text-sm font-semibold text-text-primary">验证记录</h1>
          </div>
          <AntButton
            size="small"
            type={launcherOpen ? "primary" : "text"}
            onClick={() => setLauncherOpen(!launcherOpen)}
            data-testid="launcher-toggle"
          >
            {launcherOpen ? "收起发起器" : "发起器 · 历史会话"}
          </AntButton>
        </div>

        {/* 发起器+历史会话折叠区（R5+R7-b：默认折叠不挤占骨架；逻辑零改动迁入） */}
        {launcherOpen && (
        <div className="space-y-2 rounded-lg border border-border-faint bg-bg-base/40 p-3" data-testid="launcher-panel">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wide text-text-muted">
              批量验证发起器（次级能力区） · {totalStrategies} 条策略 · {strategyGroups.length} 品种
            </span>
            <AntButton
              type={view === "history" ? "primary" : "text"}
              size="small"
              onClick={() => setView(view === "history" ? "verify" : "history")}
            >
              <History className="w-3 h-3 mr-1" />
              {view === "history" ? "返回发起器" : "历史会话"}
            </AntButton>
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
            {/* 配置工具栏 */}
            {configCollapsed && stream.status !== "running" ? (
              <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/40 border border-slate-700/30">
                <span className="text-xs text-text-secondary">
                  已验证 {filledRanges.length} 个区间 ({filledRanges.map((r) => `${r.start.slice(0, 10)}~${r.end.slice(0, 10)}`).join("、")}) · {advancedParams.init_cash} 资金
                </span>
                <AntButton type="text" size="small" onClick={() => { setConfigCollapsed(false); stream.reset(); }}>
                  重新验证
                </AntButton>
              </div>
            ) : (
              <div className="p-3 rounded-lg bg-slate-800/40 border border-slate-700/30 space-y-2">
                {/* 行 1：预设 */}
                <div className="flex items-center justify-between">
                  <div className="flex gap-1">
                    {DATE_PRESETS.map((p) => (
                      <AntButton key={p.months} type="text" size="small" className="!text-[10px] !text-text-secondary" onClick={() => applyPreset(p.months)}>
                        {p.label}
                      </AntButton>
                    ))}
                  </div>
                  <AntButton type="text" size="small" className="!text-[10px] !text-text-secondary" onClick={addDateRange}>
                    <Plus className="h-3 w-3 mr-0.5" /> 自定义区间
                  </AntButton>
                </div>

                {/* 日期区间 */}
                <div className="space-y-1">
                  {dateRanges.map((range, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        type="date"
                        value={range.start}
                        onChange={(e) => handleDateChange(i, "start", e.target.value)}
                        size="small"
                        className="text-xs flex-1"
                      />
                      <span className="text-slate-600 text-xs">~</span>
                      <Input
                        type="date"
                        value={range.end}
                        onChange={(e) => handleDateChange(i, "end", e.target.value)}
                        size="small"
                        className="text-xs flex-1"
                      />
                      {dateRanges.length > 1 && (
                        <AntButton type="text" size="small" danger onClick={() => removeDateRange(i)} aria-label="删除该日期区间">
                          <Trash2 className="h-3 w-3" />
                        </AntButton>
                      )}
                    </div>
                  ))}
                </div>

                {/* 行 2：参数+操作 */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">资金</span>
                      <InputNumber
                        step={10000}
                        min={1000}
                        value={Number(advancedParams.init_cash) || undefined}
                        onChange={(v) => setAdvancedParams((p) => ({ ...p, init_cash: String(v ?? 100000) }))}
                        size="small"
                        className="!w-24 !text-[11px]"
                      />
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">杠杆</span>
                      <InputNumber
                        step={1}
                        min={1}
                        max={10}
                        value={Number(advancedParams.leverage) || undefined}
                        onChange={(v) => setAdvancedParams((p) => ({ ...p, leverage: String(v ?? 1) }))}
                        size="small"
                        className="!w-12 !text-[11px]"
                      />
                      <span className="text-[10px] text-text-secondary">x</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">费率</span>
                      <InputNumber
                        step={0.0001}
                        min={0}
                        max={0.01}
                        value={Number(advancedParams.fee) || undefined}
                        onChange={(v) => setAdvancedParams((p) => ({ ...p, fee: String(v ?? 0.001) }))}
                        size="small"
                        className="!w-20 !text-[11px]"
                      />
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[10px] text-text-secondary shrink-0">滑点</span>
                      <InputNumber
                        step={0.0001}
                        min={0}
                        max={0.01}
                        value={Number(advancedParams.slippage) || undefined}
                        onChange={(v) => setAdvancedParams((p) => ({ ...p, slippage: String(v ?? 0.0005) }))}
                        size="small"
                        className="!w-20 !text-[11px]"
                      />
                    </div>
                  </div>
                  <AntButton size="small" type="primary" disabled={!canVerify} onClick={handleVerify}>
                    <Play className="w-3 h-3 mr-1" />
                    开始验证 ({strategies.length})
                  </AntButton>
                </div>
              </div>
            )}

            {/* 进度面板 */}
            {stream.status === "running" && (
              <div className="p-4 rounded-lg bg-slate-800/40 border border-slate-700/30 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <LoadingOutlined className="text-base text-domain-paper" />
                    <span className="text-sm font-medium text-text-primary">验证中...</span>
                  </div>
                  <span className="text-xs font-mono text-text-secondary">
                    {stream.progress.current}/{stream.progress.total} 步骤
                  </span>
                </div>
                <Progress
                  percent={stream.progress.total > 0 ? (stream.progress.current / stream.progress.total) * 100 : 0}
                  showInfo={false}
                  size={{ height: 6 }}
                  strokeColor="var(--color-domain-paper)"
                />
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-text-secondary">
                    正在处理 {stream.currentGroup} · {stream.rangeLabel}
                  </span>
                  <AntButton type="text" size="small" danger onClick={handleCancel}>
                    取消验证
                  </AntButton>
                </div>
              </div>
            )}

            {/* 错误态 */}
            {stream.status === "error" && (
              <div className="p-3 rounded-lg bg-slate-800/40 border border-red-900/30 flex items-center gap-2">
                <XIcon className="w-4 h-4 text-red-400" />
                <span className="text-xs text-red-400">{stream.error}</span>
                <AntButton type="text" size="small" onClick={() => { stream.reset(); handleVerify(); }} className="ml-auto">
                  重试
                </AntButton>
              </div>
            )}

            {/* 结果区 */}
            {stream.status === "done" && stats && (
              <>
                {/* 指标条 */}
                <div className="flex items-center gap-4 px-1">
                  <Stat label="达标" value={`${stats.qualified}/${stats.total}`} trend={stats.qualified > 0 ? "up" : "neutral"} />
                  <Stat label="均分" value={stats.avgScore.toFixed(2)} trend={stats.avgScore > 0.5 ? "up" : stats.avgScore > 0.2 ? "neutral" : "down"} />
                  <Stat label="最佳" value={stats.bestName ?? "-"} trend="up" />
                </div>

                {/* 筛选与排序 */}
                <div className="flex items-center justify-between">
                  <div className="flex gap-1">
                    {(["all", "qualified", "unqualified"] as FilterKey[]).map((k) => (
                      <AntButton
                        key={k}
                        type={filterKey === k ? "primary" : "text"}
                        size="small"
                        className="!text-[10px]"
                        onClick={() => setFilterKey(k)}
                      >
                        {k === "all" ? "全部" : k === "qualified" ? "达标" : "未达标"}
                      </AntButton>
                    ))}
                  </div>
                  <div className="flex items-center gap-1">
                    <ArrowUpDown className="w-3 h-3 text-text-secondary" />
                    {(["score", "qualified", "return"] as SortKey[]).map((k) => (
                      <AntButton
                        key={k}
                        type={sortKey === k ? "primary" : "text"}
                        size="small"
                        className="!text-[10px]"
                        onClick={() => setSortKey(k)}
                      >
                        {k === "score" ? "评分" : k === "qualified" ? "达标率" : "收益"}
                      </AntButton>
                    ))}
                  </div>
                </div>

                {/* 策略卡网格 */}
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
                        if (!s) return;
                        // 上盘流转：Trading 创建面只收 strategy_id，DNA 由后端从策略库快照
                        navigate("/trading", { state: { strategyId: s.strategy_id } });
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

            {/* 初始空态引导 */}
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
        )}

        {/* 浏览骨架主体：筛选→记录表→详情（fe-validation-records-page） */}
        <div className="space-y-2" data-testid="records-browser">
          <FilterBar onSearch={(f) => { setFilters(f); setSelectedRecord(undefined); }} />
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="rounded-lg border border-border-faint p-2">
              <RecordsTable filters={filters} onSelect={setSelectedRecord} selectedId={selectedRecord?.record_id} />
            </div>
            <div className="rounded-lg border border-border-faint p-2">
              <RecordDetail record={selectedRecord} />
            </div>
          </div>
        </div>
      </div>
    </PageTransition>
  );
}

/* ── 子组件 ── */

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
      {/* 行 1：头部——点击展开 */}
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
              <Tag className="!m-0 !text-[9px] !leading-4 !px-1 shrink-0">
                {strategy.source === "evolution" ? "进化" : strategy.source === "lab" ? "实验室" : strategy.source ?? ""}
              </Tag>
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

      {/* 行 2：区间条 */}
      <div className="flex gap-1">
        {item.per_period_metrics.map((pm, pi) => (
          <PeriodBar key={pi} pm={pm} />
        ))}
      </div>

      {/* 行 3：聚合指标 */}
      {strategy && (
        <div className="flex items-center gap-3 text-[10px] text-text-secondary">
          <span>{strategy.symbol}/{strategy.timeframe}</span>
          <AggregateMetrics periods={item.per_period_metrics} />
        </div>
      )}

      {/* 展开详情 */}
      {expanded && (
        <div className="border-t border-slate-700/30 pt-2 space-y-2">
          {/* DNA 摘要 */}
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

          {/* 逐区间明细表 */}
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

          {/* 展开操作按钮 */}
          <div className="flex gap-2 pt-1">
            <AntButton
              size="small"
              onClick={onTrade}
              disabled={!hasDna}
              style={allQualified ? { background: "var(--color-profit)", borderColor: "var(--color-profit)" } : undefined}
            >
              <TrendingUp className="w-3 h-3 mr-1" /> 开始交易
            </AntButton>
            <AntButton size="small" onClick={onBacktest} disabled={!hasDna}>
              <Eye className="w-3 h-3 mr-1" /> 查看回测
            </AntButton>
            <AntButton size="small" onClick={onEvolve} disabled={!hasDna}>
              <Dna className="w-3 h-3 mr-1" /> 继续进化
            </AntButton>
          </div>
        </div>
      )}

      {/* 收起操作按钮 */}
      {!expanded && (
        <div className="flex items-center gap-2 pt-1 border-t border-slate-700/20">
          {allQualified ? (
            <AntButton size="small" onClick={onTrade} disabled={!hasDna}
              className="!text-[10px]" style={{ background: "var(--color-profit)", borderColor: "var(--color-profit)" }}>
              <Play className="w-3 h-3 mr-0.5" /> 开始交易
            </AntButton>
          ) : (
            <AntButton size="small" onClick={onEvolve} disabled={!hasDna}
              className="!text-[10px]" style={{ background: "var(--color-domain-paper)", borderColor: "var(--color-domain-paper)" }}>
              <Dna className="w-3 h-3 mr-0.5" /> 继续优化
            </AntButton>
          )}
          <Tooltip title="查看回测">
            <AntButton type="text" size="small" onClick={onBacktest} disabled={!hasDna} aria-label="查看回测">
              <Eye className="w-3 h-3 text-text-secondary" />
            </AntButton>
          </Tooltip>
          {allQualified ? (
            <Tooltip title="继续优化">
              <AntButton type="text" size="small" onClick={onEvolve} disabled={!hasDna} aria-label="继续优化">
                <Dna className="w-3 h-3 text-text-secondary" />
              </AntButton>
            </Tooltip>
          ) : (
            <Tooltip title="模拟交易">
              <AntButton type="text" size="small" onClick={onTrade} disabled={!hasDna} aria-label="模拟交易">
                <TrendingUp className="w-3 h-3 text-text-secondary" />
              </AntButton>
            </Tooltip>
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
  const maxDD = Math.min(...periods.map((p) => p.max_drawdown));

  return (
    <>
      <span>年化 {formatReturn(avgReturn)}</span>
      <span>夏普 {avgSharpe.toFixed(2)}</span>
      <span>回撤 {(maxDD * 100).toFixed(1)}%</span>
    </>
  );
}

/* ── 历史视图 ── */

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
  // 行类型仅收窄 thresholds（生成源索引签名→三键具体形，见 VerifyThresholds）
  const rows = sessionResults?.items as VerifyHistoryRow[] | undefined;

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
                <Tag
                  className="!m-0 !text-[9px] !leading-4"
                  color={session.status === "completed" ? "success" : session.status === "failed" ? "error" : "warning"}
                >
                  {session.status === "completed" ? "完成" : session.status === "failed" ? "失败" : "运行中"}
                </Tag>
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
              <div className="px-3 pb-3 border-t border-slate-700/30 pt-2 overflow-x-auto space-y-2">
                {rows ? (
                  <>
                    {/* B5 概要形制：星级+门槛点行+关键指标（不做全量字段铺开） */}
                    <table className="w-full text-[11px]" data-testid="session-results-table">
                      <thead>
                        <tr className="border-b border-slate-700/40">
                          <th className="py-1.5 text-left text-text-secondary font-medium">策略名</th>
                          <th className="py-1.5 text-left text-text-secondary font-medium">品种/周期</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium" title="门槛达标数机械换算（0-3）">星级</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium" title="收益>0 / 回撤<25% / 交易数≥5 三门槛三态点">门槛</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium">年化</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium" title="最差区间回撤（口径取绝对值）">回撤</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium">交易</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium">Fitness</th>
                          <th className="py-1.5 text-center text-text-secondary font-medium">达标</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((item) => (
                          <tr key={item.result_id} className="border-b border-slate-700/10">
                            <td className="py-1.5 text-text-primary truncate max-w-[120px]">{item.strategy_name ?? item.strategy_id}</td>
                            <td className="py-1.5 text-text-secondary font-mono text-[10px]">{item.symbol}/{item.timeframe}</td>
                            <td className="py-1.5 text-center"><VerifyStar star={item.star} /></td>
                            <td className="py-1.5 text-center"><ThresholdDots thresholds={item.thresholds} /></td>
                            <td className={cn("py-1.5 text-center font-mono tabular-nums", item.total_return > 0 ? "text-emerald-400" : "text-red-400")}>
                              {formatReturn(item.total_return)}
                            </td>
                            <td className="py-1.5 text-center font-mono tabular-nums text-text-primary">
                              {formatDrawdown(item.max_drawdown)}
                            </td>
                            <td className="py-1.5 text-center font-mono tabular-nums text-text-primary">
                              {item.total_trades}
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
                    <ThresholdsSummary rows={rows} />
                  </>
                ) : (
                  <div className="py-4 text-center text-[11px] text-text-secondary">
                    <LoadingOutlined className="mr-1" /> 加载中...
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
