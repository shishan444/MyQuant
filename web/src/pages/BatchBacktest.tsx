/**
 * 批跑回测页（路由 /batch-backtest）。多策略×多区间批量回测：策略
 * 选择/日期区间/参数配置 → 跨区间聚合摘要（六列对齐 avg/worst/
 * qualified_count 等）+逐区间结果行与展开明细。消费方：App 路由
 * （页面入口）。
 */
import { useState, useMemo, useCallback } from "react";
import { useLocation } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "motion/react";
import {
  Play, X, ChevronDown, ChevronUp, LineChart,
  Search, Check, ArrowUpDown,
} from "lucide-react";
import { ATTENTION } from "@/theme/tokens";
import { StarRating } from "@/components/StarRating";
import { LoadingOutlined } from "@ant-design/icons";
import { Button as AntButton, Input, InputNumber, Progress } from "antd";
import { PageTransition } from "@/components/PageTransition";
import { useStrategies, useBatchBacktestStream } from "@/hooks/useStrategies";
import { fetchBatchBacktestDetail } from "@/services/strategies";
import { cn, monthsAgo, todayISO } from "@/lib/utils";
import { BacktestMetricsPanel } from "@/components/lab";
import { EquityCurve } from "@/components/charts/EquityCurve";
import type {
  BatchBacktestResultItem,
  BatchBacktestSummaryItem,
  BacktestResult,
} from "@/types/api";

/* ── 辅助函数 ── */

interface DateRange { start: string; end: string }

const DATE_PRESETS = [
  { label: "近3月", months: 3 },
  { label: "近6月", months: 6 },
  { label: "近1年", months: 12 },
] as const;

type StarFilter = "all" | "4+" | "3+";
type QualifiedFilter = "all" | "qualified" | "unqualified";

/** 结果排序键（alignment-batch D2：fitness 排序披露切换，至少两键） */
type ResultSortKey = "avg_fitness" | "avg_sharpe_ratio";

const RESULT_SORT_LABELS: Record<ResultSortKey, string> = {
  avg_fitness: "平均 Fitness",
  avg_sharpe_ratio: "平均夏普",
};

function formatReturn(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(2)}%`;
}

/** 回撤口径归一：引擎 max_drawdown 为负值小数（drawdown.min() 直落），展示取 abs+百分数 */
function formatDrawdownPct(v: number): string {
  return `${(Math.abs(v) * 100).toFixed(1)}%`;
}

/* ── 主组件 ── */

export function BatchBacktest() {
  const location = useLocation();
  const locationState = location.state as { strategy_ids?: string[] } | null;

  // ── 策略数据 ──
  const { data: strategyData, isLoading: strategiesLoading } = useQuery(
    useStrategies({ sort_by: "created_at", sort_order: "desc", limit: 500 })
  );
  const strategies = strategyData?.items ?? [];

  // ── 选择态 ──
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => {
    if (locationState?.strategy_ids?.length) {
      return new Set(locationState.strategy_ids);
    }
    return new Set();
  });
  const [starFilter, setStarFilter] = useState<StarFilter>("all");
  const [qualifiedFilter, setQualifiedFilter] = useState<QualifiedFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");

  // ── 配置态 ──
  const [dateRanges, setDateRanges] = useState<DateRange[]>([{ start: "", end: "" }]);
  const [params, setParams] = useState({
    init_cash: "100000",
    fee: "0.001",
    slippage: "0.0005",
    leverage: "1",
  });
  const [configCollapsed, setConfigCollapsed] = useState(false);

  // ── 结果态 ──
  const [resultSortKey, setResultSortKey] = useState<ResultSortKey>("avg_fitness");
  const [expandedStrategyId, setExpandedStrategyId] = useState<string | null>(null);
  const [detailMap, setDetailMap] = useState<Record<string, BacktestResult>>({});
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({});
  const [detailError, setDetailError] = useState<Record<string, string>>({});
  const [expandedResultId, setExpandedResultId] = useState<string | null>(null);

  // ── 流式 hook ──
  const stream = useBatchBacktestStream();

  // ── 筛选后策略 ──
  const filteredStrategies = useMemo(() => {
    let items = [...strategies];
    if (starFilter === "4+") items = items.filter((s) => (s.verify_star ?? 0) >= 4);
    else if (starFilter === "3+") items = items.filter((s) => (s.verify_star ?? 0) >= 3);
    if (qualifiedFilter === "qualified") items = items.filter((s) => s.qualified === true);
    else if (qualifiedFilter === "unqualified") items = items.filter((s) => s.qualified !== true);
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      items = items.filter(
        (s) =>
          (s.name ?? s.strategy_id).toLowerCase().includes(q) ||
          `${s.symbol}/${s.timeframe}`.toLowerCase().includes(q)
      );
    }
    items.sort((a, b) => (b.verify_star ?? 0) - (a.verify_star ?? 0));
    return items;
  }, [strategies, starFilter, qualifiedFilter, searchQuery]);

  const filledRanges = useMemo(() => dateRanges.filter((r) => r.start && r.end), [dateRanges]);
  const canStart = selectedIds.size >= 1 && filledRanges.length >= 1 && stream.status !== "running";

  // ── 流式结果 ──
  const summary = useMemo<BatchBacktestSummaryItem[]>(() => {
    if (stream.status === "done" && Array.isArray(stream.summary)) return stream.summary as BatchBacktestSummaryItem[];
    return [];
  }, [stream.status, stream.summary]);

  // 排序键切换呈现（D2：当前键明示披露，降序）
  const sortedSummary = useMemo(
    () => [...summary].sort((a, b) => b[resultSortKey] - a[resultSortKey]),
    [summary, resultSortKey]
  );

  const stats = useMemo(() => {
    if (summary.length === 0) return null;
    const avgReturn = summary.reduce((s, item) => s + item.avg_total_return, 0) / summary.length;
    const totalQualified = summary.reduce((s, item) => s + item.qualified_count, 0);
    const totalPeriods = summary.reduce((s, item) => s + item.total_periods, 0);
    const qualifiedRate = totalPeriods > 0 ? totalQualified / totalPeriods : 0;
    const best = summary.reduce((a, b) => (a.avg_fitness > b.avg_fitness ? a : b));
    return { total: summary.length, avgReturn, qualifiedRate, bestName: best.strategy_name };
  }, [summary]);

  // ── 选择处理 ──
  const toggleStrategy = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const selectAllFiltered = useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const s of filteredStrategies) next.add(s.strategy_id);
      return next;
    });
  }, [filteredStrategies]);

  const deselectFiltered = useCallback(() => {
    const filteredIdSet = new Set(filteredStrategies.map((s) => s.strategy_id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const id of filteredIdSet) next.delete(id);
      return next;
    });
  }, [filteredStrategies]);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  // ── 配置处理 ──
  const handleDateChange = useCallback((index: number, field: "start" | "end", value: string) => {
    setDateRanges((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  }, []);

  const applyPreset = useCallback((months: number) => {
    setDateRanges([{ start: monthsAgo(months), end: todayISO() }]);
  }, []);

  const addDateRange = useCallback(() => {
    setDateRanges((prev) => (prev.length < 3 ? [...prev, { start: "", end: "" }] : prev));
  }, []);

  const removeDateRange = useCallback((index: number) => {
    setDateRanges((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleStart = useCallback(() => {
    if (!canStart) return;
    stream.start({
      strategy_ids: Array.from(selectedIds),
      data_ranges: filledRanges,
      init_cash: parseFloat(params.init_cash) || 100000,
      fee: parseFloat(params.fee) || 0.001,
      slippage: parseFloat(params.slippage) || 0.0005,
      leverage: parseInt(params.leverage) || 1,
    });
    setConfigCollapsed(true);
  }, [canStart, selectedIds, filledRanges, params, stream]);

  const handleCancel = useCallback(() => {
    stream.cancel();
    setConfigCollapsed(false);
  }, [stream]);

  const handleLoadDetail = useCallback(async (resultId: string) => {
    if (detailMap[resultId] || detailLoading[resultId]) return;
    setDetailLoading((prev) => ({ ...prev, [resultId]: true }));
    setDetailError((prev) => ({ ...prev, [resultId]: "" }));
    try {
      const data = await fetchBatchBacktestDetail(resultId);
      setDetailMap((prev) => ({ ...prev, [resultId]: data }));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "加载失败";
      setDetailError((prev) => ({ ...prev, [resultId]: msg }));
    } finally {
      setDetailLoading((prev) => ({ ...prev, [resultId]: false }));
    }
  }, [detailMap, detailLoading]);

  const handleReset = useCallback(() => {
    stream.reset();
    setConfigCollapsed(false);
    setExpandedStrategyId(null);
    setExpandedResultId(null);
    setDetailMap({});
    setDetailLoading({});
    setDetailError({});
  }, [stream]);

  const progressPct = stream.progress.total > 0 ? (stream.progress.current / stream.progress.total) * 100 : 0;
  const allFilteredSelected = filteredStrategies.length > 0 && filteredStrategies.every((s) => selectedIds.has(s.strategy_id));

  return (
    <PageTransition>
      <div className="p-4 space-y-3">
        {/* 头部 */}
        <div className="flex items-center gap-2.5">
          <LineChart className="h-4 w-4 text-accent-gold" />
          <h1 className="text-sm font-semibold text-text-primary">批量回测</h1>
          <span className="text-[11px] font-mono text-text-secondary">
            {selectedIds.size} 条策略 · {filledRanges.length} 个区间
          </span>
        </div>

        {/* ─── 配置 ─── */}
        {!configCollapsed && stream.status !== "running" && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-xl border border-border-default/50 bg-white/[0.01] p-4 space-y-4"
          >
            {/* 策略选择器 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-text-secondary">选择策略</label>
                <span className="text-[10px] font-mono text-text-secondary">
                  已选 {selectedIds.size} / {strategies.length}
                </span>
              </div>

              {/* 筛选栏 */}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1">
                  {(["all", "4+", "3+"] as StarFilter[]).map((f) => (
                    <AntButton key={f} type={starFilter === f ? "primary" : "text"} size="small" className="!text-[10px]" onClick={() => setStarFilter(f)}>
                      {f === "all" ? "全部" : `≥${f.replace("+", "+")}星`}
                    </AntButton>
                  ))}
                  <span className="text-border-default/30 mx-0.5">|</span>
                  {(["all", "qualified", "unqualified"] as QualifiedFilter[]).map((f) => (
                    <AntButton key={f} type={qualifiedFilter === f ? "primary" : "text"} size="small" className="!text-[10px]" onClick={() => setQualifiedFilter(f)}>
                      {f === "all" ? "全部" : f === "qualified" ? "达标" : "未达标"}
                    </AntButton>
                  ))}
                </div>
                <div className="relative">
                  <Input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="搜索策略..."
                    size="small"
                    prefix={<Search className="h-3 w-3 text-text-muted" />}
                    className="!w-28 !text-[10px]"
                  />
                </div>
              </div>

              {/* 选择操作 */}
              <div className="flex items-center gap-2">
                <AntButton type="text" size="small" className="!text-[10px]" onClick={selectAllFiltered} disabled={allFilteredSelected}>
                  全选当前
                </AntButton>
                <AntButton type="text" size="small" className="!text-[10px]" onClick={clearSelection} disabled={selectedIds.size === 0}>
                  清空
                </AntButton>
              </div>

              {/* 策略表格 */}
              <div className="max-h-[220px] overflow-y-auto rounded-lg border border-border-default/30">
                {strategiesLoading ? (
                  <div className="py-6 text-center">
                    <LoadingOutlined className="text-base text-text-muted" />
                    <p className="text-[10px] text-text-secondary mt-2">加载策略列表...</p>
                  </div>
                ) : filteredStrategies.length === 0 ? (
                  <div className="py-6 text-center">
                    <p className="text-[10px] text-text-secondary">
                      {strategies.length === 0 ? "策略库为空，请先到策略库创建策略" : "无匹配策略"}
                    </p>
                  </div>
                ) : (
                  <table className="w-full text-[11px]">
                    <thead className="sticky top-0 bg-bg-surface z-10">
                      <tr className="border-b border-border-default/30 text-text-secondary">
                        <th className="py-1.5 px-2 w-8 text-center">
                          <input
                            type="checkbox"
                            checked={allFilteredSelected}
                            onChange={() => allFilteredSelected ? deselectFiltered() : selectAllFiltered()}
                            className="rounded border-border-default/50"
                          />
                        </th>
                        <th className="py-1.5 px-2 text-left font-medium">策略名称</th>
                        <th className="py-1.5 px-2 text-left font-medium">品种/周期</th>
                        <th className="py-1.5 px-2 text-center font-medium">星级</th>
                        <th className="py-1.5 px-2 text-right font-medium">来源</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredStrategies.map((s) => (
                        <tr
                          key={s.strategy_id}
                          className={cn(
                            "border-b border-border-default/10 hover:bg-white/[0.01] cursor-pointer transition-colors",
                            selectedIds.has(s.strategy_id) && "bg-accent-gold/[0.03]"
                          )}
                          onClick={() => toggleStrategy(s.strategy_id)}
                        >
                          <td className="py-1.5 px-2 text-center" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              checked={selectedIds.has(s.strategy_id)}
                              onChange={() => toggleStrategy(s.strategy_id)}
                              className="rounded border-border-default/50"
                            />
                          </td>
                          <td className="py-1.5 px-2 text-text-primary truncate max-w-[200px]">
                            {s.name ?? s.strategy_id}
                          </td>
                          <td className="py-1.5 px-2 text-text-secondary font-mono">
                            {s.symbol}/{s.timeframe}
                          </td>
                          <td className="py-1.5 px-2 text-center">
                            <StarRating value={s.verify_star} />
                          </td>
                          <td className="py-1.5 px-2 text-right">
                            <span className="text-[9px] px-1 py-0.5 rounded bg-white/[0.03] text-text-secondary">
                              {s.source === "evolution" ? "进化" : s.source === "lab" ? "实验室" : s.source ?? ""}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            {/* 日期区间 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-text-secondary">日期区间</label>
                <div className="flex gap-1">
                  {DATE_PRESETS.map((p) => (
                    <AntButton key={p.months} type="text" size="small" className="!text-[10px] !text-text-secondary" onClick={() => applyPreset(p.months)}>
                      {p.label}
                    </AntButton>
                  ))}
                  <AntButton type="text" size="small" onClick={addDateRange} className="!text-[10px] !text-text-secondary" disabled={dateRanges.length >= 3}>
                    + 自定义区间
                  </AntButton>
                </div>
              </div>
              <div className="space-y-1.5">
                {dateRanges.map((range, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <Input type="date" value={range.start} onChange={(e) => handleDateChange(i, "start", e.target.value)}
                      size="small" className="text-xs flex-1" />
                    <span className="text-text-muted text-xs">~</span>
                    <Input type="date" value={range.end} onChange={(e) => handleDateChange(i, "end", e.target.value)}
                      size="small" className="text-xs flex-1" />
                    {dateRanges.length > 1 && (
                      <AntButton type="text" size="small" onClick={() => removeDateRange(i)} aria-label="删除该日期区间">
                        <X className="h-3 w-3" />
                      </AntButton>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* 参数内联+开始 */}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-3">
                <InlineParam label="资金" value={params.init_cash} onChange={(v) => setParams((p) => ({ ...p, init_cash: v }))} w="!w-24" />
                <InlineParam label="杠杆" value={params.leverage} onChange={(v) => setParams((p) => ({ ...p, leverage: v }))} suffix="x" w="!w-12" />
                <InlineParam label="费率" value={params.fee} onChange={(v) => setParams((p) => ({ ...p, fee: v }))} w="!w-20" />
                <InlineParam label="滑点" value={params.slippage} onChange={(v) => setParams((p) => ({ ...p, slippage: v }))} w="!w-20" />
              </div>
              <AntButton type="primary" disabled={!canStart} onClick={handleStart} className="min-w-[140px]">
                <Play className="w-3.5 h-3.5 mr-1.5" />
                开始回测 ({selectedIds.size})
              </AntButton>
            </div>
          </motion.div>
        )}

        {/* 收起态配置摘要 */}
        {configCollapsed && stream.status !== "running" && (
          <div className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/40 border border-slate-700/30">
            <span className="text-xs text-text-secondary">
              已选 {selectedIds.size} 条策略 · {filledRanges.map((r) => `${r.start}~${r.end}`).join("、")} · 资金 {params.init_cash}
            </span>
            <AntButton type="text" size="small" onClick={handleReset}>重新配置</AntButton>
          </div>
        )}

        {/* ─── 进度 ─── */}
        {stream.status === "running" && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-xl border border-border-default/50 bg-white/[0.01] p-4 space-y-3"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <LoadingOutlined className="text-base text-accent-gold" />
                <span className="text-sm font-medium text-text-primary">批量回测中...</span>
              </div>
              <span className="text-xs font-mono text-text-secondary">
                {stream.progress.current}/{stream.progress.total} 步骤
              </span>
            </div>
            <Progress percent={progressPct} showInfo={false} size={{ height: 8 }} strokeColor={ATTENTION.gold} />
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-text-secondary">
                正在处理 {stream.currentGroup} · {stream.rangeLabel}
              </span>
              <div className="flex items-center gap-3">
                <span className="text-[11px] font-mono text-text-muted">已完成 {stream.results.length} 条</span>
                <AntButton type="text" size="small" danger onClick={handleCancel}>取消</AntButton>
              </div>
            </div>
          </motion.div>
        )}

        {/* ─── 错误 ─── */}
        {stream.status === "error" && (
          <div className="rounded-xl border border-red-900/30 bg-white/[0.01] p-3 flex items-center gap-2">
            <X className="w-4 h-4 text-red-400 shrink-0" />
            <span className="text-xs text-red-400 flex-1">{stream.error}</span>
            <AntButton type="text" size="small" onClick={handleReset}>重置</AntButton>
          </div>
        )}

        {/* ─── 结果 ─── */}
        {stream.status === "done" && stats && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-3">
            {/* 指标条 */}
            <div className="flex items-center gap-5 px-1">
              <StatPill label="策略数" value={`${stats.total}`} />
              <StatPill label="平均收益" value={formatReturn(stats.avgReturn)} color={stats.avgReturn >= 0 ? "text-emerald-400" : "text-red-400"} />
              <StatPill label="达标率" value={`${(stats.qualifiedRate * 100).toFixed(0)}%`} color="text-accent-gold" />
              <div className="flex items-baseline gap-1.5">
                <span className="text-[10px] text-text-secondary">最佳</span>
                <span className="text-xs font-medium text-text-primary truncate max-w-[120px]">{stats.bestName}</span>
              </div>
            </div>

            {/* 排序键披露切换（D2） */}
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-text-secondary" data-testid="batch-sort-label">
                当前按 {RESULT_SORT_LABELS[resultSortKey]} 降序
              </span>
              <div className="flex items-center gap-1">
                <ArrowUpDown className="w-3 h-3 text-text-secondary" />
                {(Object.keys(RESULT_SORT_LABELS) as ResultSortKey[]).map((k) => (
                  <AntButton
                    key={k}
                    type={resultSortKey === k ? "primary" : "text"}
                    size="small"
                    className="!text-[10px]"
                    data-testid={`batch-sort-btn-${k}`}
                    onClick={() => setResultSortKey(k)}
                  >
                    {RESULT_SORT_LABELS[k]}
                  </AntButton>
                ))}
              </div>
            </div>

            {/* 策略结果行 — 六列对齐（avg_total_return/avg_sharpe_ratio/worst_max_drawdown/avg_fitness/qualified_count/total_periods） */}
            <div className="space-y-1">
              <div className="grid grid-cols-[minmax(150px,1.4fr)_repeat(6,minmax(68px,0.75fr))_28px] gap-2 px-3 pb-1 text-[10px] text-text-secondary">
                <span>策略</span>
                <span className="text-right" data-testid="batch-head-avg_total_return">平均收益</span>
                <span className="text-right" data-testid="batch-head-avg_sharpe_ratio">平均夏普</span>
                <span className="text-right" data-testid="batch-head-worst_max_drawdown" title="最差区间回撤（口径取绝对值）">最差回撤</span>
                <span className="text-right" data-testid="batch-head-avg_fitness">平均Fitness</span>
                <span className="text-right" data-testid="batch-head-qualified_count">达标数</span>
                <span className="text-right" data-testid="batch-head-total_periods">区间数</span>
                <span />
              </div>
              {sortedSummary.map((item) => (
                <StrategyResultRow
                  key={item.strategy_id}
                  item={item}
                  expanded={expandedStrategyId === item.strategy_id}
                  onToggle={() => setExpandedStrategyId(expandedStrategyId === item.strategy_id ? null : item.strategy_id)}
                  detailMap={detailMap}
                  detailLoading={detailLoading}
                  detailError={detailError}
                  onLoadDetail={handleLoadDetail}
                  expandedResultId={expandedResultId}
                  setExpandedResultId={setExpandedResultId}
                />
              ))}
            </div>

            {summary.length === 0 && (
              <div className="py-12 text-center">
                <LineChart className="w-8 h-8 text-text-muted mx-auto mb-3" />
                <p className="text-sm text-text-secondary">无回测结果</p>
              </div>
            )}
          </motion.div>
        )}
      </div>
    </PageTransition>
  );
}

/* ── 子组件 ── */

function InlineParam({ label, value, onChange, suffix, w = "!w-20" }: {
  label: string; value: string; onChange: (v: string) => void; suffix?: string; w?: string;
}) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-[10px] text-text-secondary shrink-0">{label}</span>
      <InputNumber value={Number(value) || undefined} onChange={(v) => onChange(String(v ?? 0))}
        size="small" className={cn("!text-[11px]", w)} aria-label={label} />
      {suffix && <span className="text-[10px] text-text-secondary">{suffix}</span>}
    </div>
  );
}

function StatPill({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-[10px] text-text-secondary">{label}</span>
      <span className={cn("text-sm font-semibold font-mono", color ?? "text-text-primary")}>{value}</span>
    </div>
  );
}

function StrategyResultRow({
  item, expanded, onToggle, detailMap, detailLoading, detailError,
  onLoadDetail, expandedResultId, setExpandedResultId,
}: {
  item: BatchBacktestSummaryItem; expanded: boolean; onToggle: () => void;
  detailMap: Record<string, BacktestResult>; detailLoading: Record<string, boolean>;
  detailError: Record<string, string>; onLoadDetail: (id: string) => void;
  expandedResultId: string | null; setExpandedResultId: (id: string | null) => void;
}) {
  const qualifiedRate = item.total_periods > 0 ? item.qualified_count / item.total_periods : 0;

  return (
    <div className="rounded-lg border border-border-default/30 overflow-hidden">
      <button
        className="w-full grid grid-cols-[minmax(150px,1.4fr)_repeat(6,minmax(68px,0.75fr))_28px] gap-2 px-3 py-2.5 items-center text-left hover:bg-white/[0.01] transition-colors"
        onClick={onToggle}
      >
        <span className="min-w-0" data-testid={`batch-result-row-${item.strategy_id}`}>
          <span className="text-xs text-text-primary font-medium truncate block max-w-[180px]">{item.strategy_name}</span>
          <span className="text-[10px] font-mono text-text-muted">{item.symbol}/{item.timeframe}</span>
        </span>
        <span className={cn("text-xs font-mono font-semibold text-right", item.avg_total_return >= 0 ? "text-emerald-400" : "text-red-400")}>
          {formatReturn(item.avg_total_return)}
        </span>
        <span className="text-[10px] font-mono text-right text-text-primary">
          {item.avg_sharpe_ratio.toFixed(2)}
        </span>
        <span className="text-[10px] font-mono text-right text-text-primary">
          {formatDrawdownPct(item.worst_max_drawdown)}
        </span>
        <span className="text-[10px] font-mono text-right text-accent-gold">
          {item.avg_fitness.toFixed(2)}
        </span>
        <span className={cn("text-[10px] font-mono text-right", qualifiedRate >= 1 ? "text-emerald-400" : qualifiedRate > 0 ? "text-amber-400" : "text-text-muted")}>
          {item.qualified_count}
        </span>
        <span className="text-[10px] font-mono text-right text-text-secondary">
          {item.total_periods}
        </span>
        {expanded ? <ChevronUp className="h-3.5 w-3.5 text-text-secondary" /> : <ChevronDown className="h-3.5 w-3.5 text-text-secondary" />}
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="border-t border-border-default/30 px-3 py-2">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="border-b border-border-default/30 text-text-secondary">
                    <th className="py-1 text-left font-medium">起始</th>
                    <th className="py-1 text-left font-medium">结束</th>
                    <th className="py-1 text-center font-medium">收益</th>
                    <th className="py-1 text-center font-medium">夏普</th>
                    <th className="py-1 text-center font-medium">回撤</th>
                    <th className="py-1 text-center font-medium">胜率</th>
                    <th className="py-1 text-center font-medium">交易</th>
                    <th className="py-1 text-center font-medium">Fitness</th>
                    <th className="py-1 text-center font-medium">达标</th>
                    <th className="py-1 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {item.per_period_results.map((r) => (
                    <PeriodDetailRow
                      key={r.result_id}
                      result={r}
                      detail={detailMap[r.result_id]}
                      detailLoading={detailLoading[r.result_id] ?? false}
                      detailError={detailError[r.result_id] ?? ""}
                      onLoadDetail={onLoadDetail}
                      expanded={expandedResultId === r.result_id}
                      onToggleDetail={() => setExpandedResultId(expandedResultId === r.result_id ? null : r.result_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function PeriodDetailRow({
  result, detail, detailLoading, detailError, onLoadDetail, expanded, onToggleDetail,
}: {
  result: BatchBacktestResultItem; detail?: BacktestResult; detailLoading: boolean;
  detailError: string; onLoadDetail: (id: string) => void; expanded: boolean; onToggleDetail: () => void;
}) {
  return (
    <>
      <tr className="border-b border-border-default/10 hover:bg-white/[0.005]">
        <td className="py-1.5 text-text-secondary font-mono">{result.data_start?.slice(0, 10) ?? "-"}</td>
        <td className="py-1.5 text-text-secondary font-mono">{result.data_end?.slice(0, 10) ?? "-"}</td>
        <td className={cn("py-1.5 text-center font-mono", result.total_return >= 0 ? "text-emerald-400" : "text-red-400")}>
          {formatReturn(result.total_return)}
        </td>
        <td className="py-1.5 text-center font-mono text-text-primary">{result.sharpe_ratio.toFixed(2)}</td>
        <td className="py-1.5 text-center font-mono text-text-primary">{(result.max_drawdown * 100).toFixed(1)}%</td>
        <td className="py-1.5 text-center font-mono text-text-primary">{result.win_rate != null ? (result.win_rate * 100).toFixed(1) + "%" : "—"}</td>
        <td className="py-1.5 text-center font-mono text-text-primary">{result.total_trades}</td>
        <td className="py-1.5 text-center font-mono text-accent-gold">{result.fitness.toFixed(2)}</td>
        <td className="py-1.5 text-center">
          {result.qualified ? <Check className="w-3 h-3 text-emerald-400 inline" /> : <X className="w-3 h-3 text-red-400/60 inline" />}
        </td>
        <td className="py-1.5 text-right">
          <AntButton type="text" size="small" onClick={() => { if (!expanded) onLoadDetail(result.result_id); onToggleDetail(); }} className="!text-[10px]">
            {expanded ? "收起" : "详情"}
          </AntButton>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={10} className="p-0">
            <div className="px-4 py-3 bg-white/[0.005] border-b border-border-default/10 space-y-3">
              {detailLoading && (
                <div className="flex items-center gap-2 text-xs text-text-secondary">
                  <LoadingOutlined className="mr-1" />加载详情...
                </div>
              )}
              {detailError && <div className="text-xs text-red-400">{detailError}</div>}
              {detail && (
                <>
                  <div>
                    <div className="text-[10px] text-text-secondary mb-1">资金曲线</div>
                    <EquityCurve data={detail.equity_curve ?? []} height={200} />
                  </div>
                  <BacktestMetricsPanel result={detail} />
                </>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
