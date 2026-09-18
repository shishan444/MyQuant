/** Scene verification mode panel: template selection + chart + annotations + results. */
import { useState, useCallback, useEffect, useRef } from "react";
import { Play, Crosshair, Minus, Square, X, Eye } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import type { IChartApi, ISeriesApi } from "lightweight-charts";

import { GlassCard } from "@/components/GlassCard";
import { Button as AntButton, Select as AntSelect } from "antd";
import { EmptyState } from "@/components/EmptyState";
import { KlineChart } from "@/components/charts/KlineChart";
import { AnnotationLayer } from "@/components/charts/AnnotationLayer";
import type { Annotation } from "@/components/charts/AnnotationLayer";
import { cn } from "@/lib/utils";

import { SceneSelector } from "./SceneSelector";
import { SceneResult } from "./SceneResult";

import { useAvailableSources } from "@/hooks/useDatasets";
import { useChartIndicators } from "@/hooks/useChartIndicators";
import type { SubChartType } from "@/hooks/useChartIndicators";
import { getSceneTypes, verifyScene } from "@/services/scene";
import { SYMBOL_OPTIONS, TIMEFRAME_SELECT_OPTIONS } from "@/lib/constants";
import type { SceneVerifyResponse, SceneTriggerDetail } from "@/types/scene";

// Default params per scene type (sub-patterns inherit from parent)
const SCENE_DEFAULT_PARAMS: Record<string, Record<string, number>> = {
  double_top: { lookback: 5, confirmation_bars: 5, min_prominence_pct: 0.5 },
  head_shoulders_top: { lookback: 5, confirmation_bars: 5, min_prominence_pct: 0.5 },
  triple_top: { lookback: 5, confirmation_bars: 5, min_prominence_pct: 0.5 },
  volume_spike: { multiplier: 2.5, avg_period: 20 },
  mean_reversion: { ma_period: 50, deviation_pct: 3.0 },
  volume_breakout: { volume_multiplier: 2.0 },
  support_resistance: { proximity_pct: 1.0 },
  cross_timeframe: { fast_period: 20, slow_period: 50 },
};

const SCENE_DEFAULT_DIRECTION: Record<string, string> = {
  support_resistance: "support",
  mean_reversion: "below",
};

/** Merge multiple SceneVerifyResponse into a single combined view. */
function mergeResponses(responses: SceneVerifyResponse[]): SceneVerifyResponse {
  if (responses.length === 0) {
    return {
      scene_type: "", scene_label: "", scene_description: "",
      total_triggers: 0, statistics_by_horizon: [],
      trigger_details: [], warnings: ["No responses to merge"],
    };
  }
  if (responses.length === 1) return responses[0];

  const allDetails = responses.flatMap((r, idx) =>
    r.trigger_details.map((t) => ({ ...t, id: idx * 1000 + t.id }))
  );
  const totalTriggers = responses.reduce((s, r) => s + r.total_triggers, 0);
  const labels = responses.map((r) => r.scene_label).filter(Boolean);
  const warnings = responses.flatMap((r) => r.warnings);

  // Use statistics from the first response as representative
  const stats = responses[0].statistics_by_horizon;

  return {
    scene_type: responses.map((r) => r.scene_type).join(","),
    scene_label: labels.join(" + "),
    scene_description: `${totalTriggers} triggers from ${responses.length} types`,
    total_triggers: totalTriggers,
    statistics_by_horizon: stats,
    trigger_details: allDetails,
    warnings,
  };
}

interface SceneModePanelProps {
  initialSymbol?: string;
  initialTimeframe?: string;
}

export function SceneModePanel({
  initialSymbol = "BTCUSDT",
  initialTimeframe = "4h",
}: SceneModePanelProps) {
  // Data source
  const [symbol, setSymbol] = useState(initialSymbol);
  const [timeframe, setTimeframe] = useState(initialTimeframe);
  const [dataStart, setDataStart] = useState(() => {
    try { return localStorage.getItem("scene_dataStart") ?? ""; } catch { return ""; }
  });
  const [dataEnd, setDataEnd] = useState(() => {
    try { return localStorage.getItem("scene_dataEnd") ?? ""; } catch { return ""; }
  });

  // Persist date range on change
  useEffect(() => {
    try {
      if (dataStart) localStorage.setItem("scene_dataStart", dataStart);
      if (dataEnd) localStorage.setItem("scene_dataEnd", dataEnd);
    } catch {}
  }, [dataStart, dataEnd]);

  // Scene selection - multi-select
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [params, setParams] = useState<Record<string, number>>({});
  const [direction, setDirection] = useState("support");

  // Result
  const [result, setResult] = useState<SceneVerifyResponse | null>(null);

  // Loading state for parallel requests
  const [isValidating, setIsValidating] = useState(false);

  // Chart + annotation state
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartApiRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [activeTool, setActiveTool] = useState<"line" | "box" | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);

  // Sub-chart indicator type
  const [subChartType, setSubChartType] = useState<SubChartType>("volume");

  // Shared chart indicators hook (replaces manual OHLCV + indicator fetching)
  const {
    candleData,
    chartIndicators,
    chartBollData,
    volumeData,
    macdData,
    kdjData,
  } = useChartIndicators({
    symbol,
    timeframe,
    dateRange: { start: dataStart, end: dataEnd },
    subChartType,
  });

  // Fetch scene types
  const { data: typesData } = useQuery({
    queryKey: ["sceneTypes"],
    queryFn: getSceneTypes,
    staleTime: Infinity,
  });
  const sceneTypes = typesData?.types ?? [];

  // Available sources for symbol dropdown
  const { data: sourcesData } = useQuery(useAvailableSources());
  const dynamicSymbols = (() => {
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
  })();

  // Toggle a scene type in the multi-select
  const handleToggleType = useCallback((typeId: string) => {
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(typeId)) {
        next.delete(typeId);
      } else {
        next.add(typeId);
      }
      return next;
    });
    setResult(null);
  }, []);

  // Update params when selected types change
  useEffect(() => {
    if (selectedTypes.size > 0) {
      // Merge default params from all selected types
      const merged: Record<string, number> = {};
      for (const id of selectedTypes) {
        Object.assign(merged, SCENE_DEFAULT_PARAMS[id] ?? {});
      }
      setParams(merged);

      // Direction from first selected type that needs it
      for (const id of selectedTypes) {
        if (id in SCENE_DEFAULT_DIRECTION) {
          setDirection(SCENE_DEFAULT_DIRECTION[id]);
          break;
        }
      }
    }
  }, [selectedTypes]);

  const handleChartReady = useCallback((chart: IChartApi, series: ISeriesApi<"Candlestick">) => {
    chartApiRef.current = chart;
    candleSeriesRef.current = series;
  }, []);

  const handleAnnotationComplete = useCallback((ann: Annotation) => {
    setAnnotations((prev) => [...prev, ann]);
    setActiveTool(null);
  }, []);

  const handleRemoveAnnotation = useCallback((id: string) => {
    setAnnotations((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const handleSubmit = useCallback(async () => {
    if (selectedTypes.size === 0) return;

    setIsValidating(true);
    try {
      const mergedParams: Record<string, unknown> = { ...params };
      for (const id of selectedTypes) {
        if (id === "support_resistance" || id === "mean_reversion") {
          mergedParams.direction = direction;
        }
      }

      const lineAnnotation = annotations.find((a) => a.type === "line");
      if (lineAnnotation?.price != null) {
        mergedParams.annotation_price = lineAnnotation.price;
      }

      // Fire parallel requests for each selected type
      const requests = Array.from(selectedTypes).map((sceneType) =>
        verifyScene({
          symbol,
          timeframe,
          scene_type: sceneType,
          params: mergedParams,
          horizons: [6, 12, 24, 48],
          data_start: dataStart || undefined,
          data_end: dataEnd || undefined,
        })
      );

      const responses = await Promise.all(requests);
      const merged = mergeResponses(responses);
      setResult(merged);
    } catch (err) {
      toast.error("场景验证失败，请检查参数后重试");
    } finally {
      setIsValidating(false);
    }
  }, [selectedTypes, params, direction, annotations, symbol, timeframe, dataStart, dataEnd]);

  const handleLocateTrigger = useCallback(
    (_trigger: SceneTriggerDetail) => {
      // Future: scroll chart to trigger time
    },
    [],
  );

  // Convert triggers to chart markers (scene-specific logic, not part of shared hook)
  const chartTriggers = result?.trigger_details?.slice(0, 50).map((t) => ({
    id: t.id,
    time: t.timestamp,
    matched: (t.forward_stats["6"]?.close_pct ?? 0) > 0,
    subtype: t.pattern_subtype,
  })) ?? [];

  const hasSelection = selectedTypes.size > 0;

  return (
    <div className="flex flex-col gap-4">
      {/* Data source row */}
      <div className="flex items-center gap-3">
        <span className="w-14 shrink-0 text-xs text-slate-400">数据源</span>
        <AntSelect
          size="small"
          value={symbol}
          onChange={(v) => { setSymbol(v); setResult(null); }}
          className="!w-28 !text-xs"
          aria-label="场景数据源"
          options={[...dynamicSymbols]}
        />
        <AntSelect
          size="small"
          value={timeframe}
          onChange={(v) => { setTimeframe(v); setResult(null); }}
          className="!w-20 !text-xs"
          aria-label="场景周期"
          options={TIMEFRAME_SELECT_OPTIONS.map((opt) => ({ value: opt.value, label: opt.label }))}
        />
        <input
          type="date"
          value={dataStart}
          onChange={(e) => setDataStart(e.target.value)}
          className="h-7 rounded border border-slate-700/50 bg-transparent px-2 text-xs text-slate-300"
        />
        <span className="text-[11px] text-slate-600">~</span>
        <input
          type="date"
          value={dataEnd}
          onChange={(e) => setDataEnd(e.target.value)}
          className="h-7 rounded border border-slate-700/50 bg-transparent px-2 text-xs text-slate-300"
        />
      </div>

      {/* Scene selector - multi-select */}
      <SceneSelector
        types={sceneTypes}
        selectedIds={selectedTypes}
        onToggle={handleToggleType}
        params={params}
        onParamsChange={setParams}
        direction={direction}
        onDirectionChange={setDirection}
      />

      {/* Chart with annotations */}
      {hasSelection && candleData && candleData.length > 0 && (
        <GlassCard className="p-3" hover={false}>
          {/* Annotation toolbar */}
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] text-slate-500">标注工具</span>
            <button
              type="button"
              title="画水平线"
              aria-pressed={activeTool === "line"}
              onClick={() => setActiveTool(activeTool === "line" ? null : "line")}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
                activeTool === "line"
                  ? "bg-amber-400/20 text-amber-400"
                  : "bg-slate-800/30 text-slate-500 hover:text-slate-400",
              )}
            >
              <Minus className="h-3 w-3" />
              水平线
            </button>
            <button
              type="button"
              title="框选区域"
              aria-pressed={activeTool === "box"}
              onClick={() => setActiveTool(activeTool === "box" ? null : "box")}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
                activeTool === "box"
                  ? "bg-purple-400/20 text-purple-400"
                  : "bg-slate-800/30 text-slate-500 hover:text-slate-400",
              )}
            >
              <Square className="h-3 w-3" />
              框选
            </button>

            {/* Sub-chart indicator selector */}
            <div className="flex items-center gap-0.5 ml-auto">
              <span className="text-[11px] text-slate-500 mr-1">副图</span>
              {(["volume", "macd", "rsi", "kdj"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setSubChartType(t)}
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[11px] transition-colors",
                    subChartType === t
                      ? "bg-sky-400/20 text-sky-400"
                      : "bg-slate-800/30 text-slate-500 hover:text-slate-400",
                  )}
                >
                  {t.toUpperCase()}
                </button>
              ))}
            </div>

            {/* Active annotations list */}
            {annotations.length > 0 && (
              <div className="flex items-center gap-1 ml-2">
                {annotations.map((ann) => (
                  <span
                    key={ann.id}
                    className="flex items-center gap-1 rounded bg-slate-800/50 px-1.5 py-0.5 text-[10px] text-slate-400"
                  >
                    {ann.type === "line" ? `@${ann.label}` : "box"}
                    <button
                      type="button"
                      className="text-slate-600 hover:text-rose-400"
                      onClick={() => handleRemoveAnnotation(ann.id)}
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {activeTool && (
              <span className="text-[10px] text-amber-500 ml-auto">
                点击图表放置标注
              </span>
            )}
          </div>

          {/* Chart container with annotation overlay */}
          <div ref={chartContainerRef} className="relative">
            <KlineChart
              data={candleData}
              indicators={chartIndicators}
              bollData={chartBollData}
              volumeData={volumeData}
              triggers={chartTriggers}
              height={550}
              onChartReady={handleChartReady}
              subChartType={subChartType}
              macdData={macdData}
              kdjData={kdjData}
            />
            <AnnotationLayer
              chartApi={chartApiRef.current}
              candleSeries={candleSeriesRef.current}
              annotations={annotations}
              activeTool={activeTool}
              onAnnotationComplete={handleAnnotationComplete}
              chartContainerRef={chartContainerRef}
            />
          </div>
        </GlassCard>
      )}

      {/* Submit */}
      <AntButton
        type="primary"
        block
        disabled={!hasSelection || isValidating}
        onClick={handleSubmit}
      >
        {activeTool ? <Eye className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
        {isValidating ? "验证中..." : `验证场景 (${selectedTypes.size})`}
      </AntButton>

      {/* Result */}
      {result ? (
        <GlassCard className="p-4" hover={false}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-slate-200">
              {result.scene_label} - 验证结果
            </h3>
            <span className="text-[11px] text-slate-500">
              {result.scene_description}
            </span>
          </div>
          <SceneResult result={result} onLocateTrigger={handleLocateTrigger} />
        </GlassCard>
      ) : !hasSelection ? (
        <GlassCard className="p-4" hover={false}>
          <EmptyState
            icon={Crosshair}
            title="选择场景模板"
            description="选择一个或多个市场场景，系统将自动检测历史数据中的所有触发点，并计算后续收益分布统计。也可以使用标注工具在图表上画线进行验证。"
          />
        </GlassCard>
      ) : null}
    </div>
  );
}
