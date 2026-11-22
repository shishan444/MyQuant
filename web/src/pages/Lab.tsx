/**
 * 策略实验室页（路由 /lab）：hypothesis 单模式——条件构建器+
 * K 线图表（数据源联动）。消费方：App 路由（页面入口+index 重定向）。
 *
 * 模式退场记录（fix-replay-gap-alignment U4/U5）：回测模式与场景验证
 * 模式随死端点退役（/api/strategies/backtest 族、/api/validate 族、
 * /api/validate/scene）；「验证策略」动作链（useValidateRules →
 * POST /api/validate/rules）与「保存为策略」链（依赖验证结果）同批
 * 退役——本页保留构建器与图表的观察形态。
 */
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { ChevronDown, ChevronUp, RotateCcw } from "lucide-react";

import { HypothesisStepper } from "@/components/hypothesis/HypothesisStepper";
import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { KlineChart } from "@/components/charts/KlineChart";
import type { KlineChartHandle } from "@/components/charts/KlineChart";
import { ChartToolbar } from "@/components/charts/ChartToolbar";
import { Select as AntSelect, Skeleton } from "antd";

import { RuleConditionGroup } from "@/components/lab";

import { useAvailableSources } from "@/hooks/useDatasets";
import { useChartIndicators } from "@/hooks/useChartIndicators";
import type { SubChartType } from "@/hooks/useChartIndicators";
import { SYMBOL_OPTIONS, TIMEFRAME_SELECT_OPTIONS } from "@/lib/constants";
import type { RuleCondition } from "@/types/api";
import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const QUICK_DATES = [
  { label: "近3月", days: 90 },
  { label: "近6月", days: 180 },
  { label: "近1年", days: 365 },
  { label: "今年", days: -1 },
  { label: "全部", days: 0 },
];

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

/** Date → 本地时区 YYYY-MM-DD（避免 toISOString 的 UTC 偏移）。 */
function toLocalDateStr(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
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
// 组件
// ---------------------------------------------------------------------------

// 输入框样式常量（CARD-C：date 档——宽度为唯一变量，其余类名共享）
const DATE_INPUT_CLS =
  "h-8 w-28 cursor-pointer rounded-md border border-border-default bg-bg-surface px-2 text-xs text-text-primary outline-none focus:border-accent-gold [::-webkit-calendar-picker-indicator]:cursor-pointer";

/** 按标的过滤数据源→周期去重选项（CARD-C：reduce/find O(n²) 与 Set 两份
 *  同逻辑实现收口为单实现——空源/无匹配回退全档常量） */
function buildTimeframeOptions(
  sources: Array<{ symbol: string; timeframe: string }> | undefined,
  symbol: string,
): { value: string; label: string }[] {
  if (!sources?.length) return [...TIMEFRAME_SELECT_OPTIONS];
  const seen = new Set<string>();
  const opts: { value: string; label: string }[] = [];
  for (const s of sources) {
    if (s.symbol === symbol && !seen.has(s.timeframe)) {
      seen.add(s.timeframe);
      opts.push({ value: s.timeframe, label: s.timeframe });
    }
  }
  return opts.length > 0 ? opts : [...TIMEFRAME_SELECT_OPTIONS];
}

export function Lab() {
  // -- Builder state --
  const [pair, setPair] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("15m");
  const [dateRange, setDateRange] = useState(getDefaultDates);
  const [entryConditions, setEntryConditions] = useState<RuleCondition[]>([]);
  const [exitConditions, setExitConditions] = useState<RuleCondition[]>([]);
  const [builderCollapsed, setBuilderCollapsed] = useState(false);
  // K 线常驻（fe-research-execution-domain 1.2）：中列 K 线全程在页——随数据源拉取呈现
  const hypoSubChartType: SubChartType = "volume";

  // -- Refs --
  const chartRef = useRef<KlineChartHandle>(null);

  // -- 图表指标 hook --
  const {
    candleData,
    chartIndicators,
    chartBollData,
    volumeData,
    macdData: hypoMacdData,
    kdjData: hypoKdjData,
    isLoadingOhlcv,
    isLoadingIndicators,
  } = useChartIndicators({
    symbol: pair,
    timeframe,
    dateRange,
    subChartType: hypoSubChartType,
    enabled: true,
  });
  const isLoading = isLoadingOhlcv || isLoadingIndicators;

  // -- 可用数据源 --
  const { data: sourcesData } = useQuery(useAvailableSources());

  // 从可用数据源构建动态 symbol/timeframe 选项
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

  const dynamicTimeframeOptions = useMemo(
    () => buildTimeframeOptions(sourcesData?.sources, pair),
    [sourcesData, pair],
  );

  // 自动设置缺省日期区间
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

  // -- baseTimeframe = timeframe（数据源周期） --
  const baseTimeframe = timeframe;

  // 收集条件构建器引用的全部 timeframe
  const availableTimeframes = useMemo(() => {
    const tfs = new Set<string>();
    tfs.add(baseTimeframe);
    for (const c of [...entryConditions, ...exitConditions]) {
      if (c.timeframe) tfs.add(c.timeframe);
    }
    // 另从动态选项补常用 timeframe
    for (const o of dynamicTimeframeOptions) {
      tfs.add(o.value);
    }
    // 按时长排序（最长在前）
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

  const handleClearAll = useCallback(() => {
    setEntryConditions([]);
    setExitConditions([]);
  }, []);

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
        {/* 02-B1 五步流水线条——①构建/②③观察（验证与保存动作链随
            fix-replay-gap-alignment 退役，进度锚定构建步） */}
        <HypothesisStepper
          current={0}
          furthest={0}
          onStepClick={(i: number) => {
            if (i === 2) {
              // 步③案例检视=中列 K 线信号面锚点
              document
                .getElementById("lab-kline-section")
                ?.scrollIntoView({ behavior: "smooth", block: "center" });
            }
          }}
        />

        {/* hypothesis 双列主体（02-B2 构建面板 / B3 图表主列） */}
        <div className="grid items-start gap-4 lg:grid-cols-[290px_1fr]">
          {/* 左列：构建器 */}
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
                {/* 数据源行 */}
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
                      className={DATE_INPUT_CLS}
                    />
                    <span className="text-xs text-text-muted">~</span>
                    <input
                      type="date"
                      value={dateRange.end}
                      onChange={(e) =>
                        setDateRange((prev) => ({ ...prev, end: e.target.value }))
                      }
                      className={DATE_INPUT_CLS}
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

                {/* 入场规则 */}
                <RuleConditionGroup
                  title="入场规则"
                  description="全部满足则买入"
                  conditions={entryConditions}
                  onConditionsChange={setEntryConditions}
                  availableTimeframes={availableTimeframes}
                />

                {/* 出场规则 */}
                <RuleConditionGroup
                  title="出场规则"
                  description="任一满足则卖出"
                  conditions={exitConditions}
                  onConditionsChange={setExitConditions}
                  availableTimeframes={availableTimeframes}
                />

                {/* 操作按钮 */}
                <div className="flex items-center justify-between pt-2">
                  <button
                    type="button"
                    onClick={handleClearAll}
                    className="flex items-center gap-1 text-xs text-text-muted transition-colors hover:text-loss"
                  >
                    <RotateCcw className="h-3 w-3" />
                    清空全部
                  </button>
                </div>
              </div>
            )}
          </GlassCard>

          {/* 中列：K 线常驻（02-B3 图表主列·主体系紫底——随数据源呈现） */}
          <GlassCard className="p-4" hover={false} id="lab-kline-section">
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-3 text-xs text-text-muted">
                <span className="font-medium text-text-secondary">
                  {pair} / {timeframe}
                </span>
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
            {isLoading ? (
              <Skeleton className="h-[450px] w-full rounded-lg" />
            ) : (
              <KlineChart
                ref={chartRef}
                data={candleData ?? []}
                indicators={chartIndicators}
                bollData={chartBollData}
                signals={[]}
                height={520}
                volumeData={volumeData}
                subChartType={hypoSubChartType}
                macdData={hypoMacdData}
                kdjData={hypoKdjData}
              />
            )}
          </GlassCard>
        </div>
      </div>
    </PageTransition>
  );
}
