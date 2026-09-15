import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useLocation } from "react-router";
import { Dna } from "lucide-react";
import { toast } from "sonner";

import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { EmptyState } from "@/components/EmptyState";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Skeleton } from "@/components/ui/skeleton";

import { SegmentedControl } from "@/components/evolution/SegmentedControl";
import type { ExploreMode } from "@/components/evolution/SegmentedControl";
import { AutoConfigForm } from "@/components/evolution/AutoConfigForm";
import { SeedConfigForm } from "@/components/evolution/SeedConfigForm";
import { ProgressPanel } from "@/components/evolution/ProgressPanel";
import { ScoreTrendChart } from "@/components/evolution/ScoreTrendChart";
import { StrategyList } from "@/components/evolution/StrategyList";
import { HistoryTable } from "@/components/evolution/HistoryTable";
import { QuickPresets } from "@/components/evolution/QuickPresets";
import { TaskDetailDrawer } from "@/components/evolution/TaskDetailDrawer";
import type { Preset } from "@/components/evolution/QuickPresets";

import {
  useEvolutionTasks,
  useEvolutionTask,
  useEvolutionHistory,
  useCreateEvolutionTask,
  useStopEvolutionTask,
  usePauseEvolutionTask,
  useResumeEvolutionTask,
  useEvolutionWebSocket,
  useDiscoveredStrategies,
} from "@/hooks/useEvolution";
import { useAvailableSources } from "@/hooks/useDatasets";
import { useCreateStrategy } from "@/hooks/useStrategies";
import { isActiveStatus, SCORE_TEMPLATE_LABELS } from "@/lib/constants";
import type { EvolutionTask, DNA, DiscoveredStrategy, RequirementsConfig } from "@/types/api";

// ---------------------------------------------------------------------------
// Main Page Component
// ---------------------------------------------------------------------------

function formatTimeframeDisplay(task: EvolutionTask): string {
  return task.timeframe_pool && task.timeframe_pool.length > 1
    ? task.timeframe_pool.join("+").toUpperCase()
    : task.timeframe.toUpperCase();
}

export function Evolution() {
  const navigate = useNavigate();
  const location = useLocation();

  // --- Mode selection state ---
  const [selectedMode, setSelectedMode] = useState<ExploreMode>("auto");
  const [seedDna, setSeedDna] = useState<DNA | null>(null);

  // Read seed DNA from route state (e.g. from Lab hypothesis validation)
  useEffect(() => {
    const state = location.state as { seedDna?: DNA } | null;
    if (state?.seedDna) {
      setSeedDna(state.seedDna);
      setSelectedMode("seed");
      window.history.replaceState({}, "");
    }
  }, []);

  // --- Expanded strategy ---
  const [expandedStrategyId, setExpandedStrategyId] = useState<string | null>(null);

  // --- Stop confirm dialog ---
  const [stopTarget, setStopTarget] = useState<EvolutionTask | null>(null);

  // --- Config collapsed state ---
  const [configCollapsed, setConfigCollapsed] = useState(false);

  // --- Preset selection (passed to AutoConfigForm to seed state) ---
  const [presetToApply, setPresetToApply] = useState<Preset | null>(null);

  // --- Last active task ID (for chart data retention after task completes) ---
  const [lastActiveTaskId, setLastActiveTaskId] = useState<string>("");

  // --- Task detail drawer ---
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);

  // --- Refs ---
  const configRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  // --- Data fetching ---
  const { data: tasksData, isLoading: tasksLoading } = useQuery(
    useEvolutionTasks({ limit: 20 })
  );

  // --- Available sources for dynamic symbol options ---
  const { data: sourcesData } = useQuery(useAvailableSources());
  const dynamicSymbolOptions = useMemo(() => {
    if (!sourcesData?.sources?.length) return undefined;
    const seen = new Set<string>();
    const opts: { value: string; label: string }[] = [];
    for (const s of sourcesData.sources) {
      if (!seen.has(s.symbol)) {
        seen.add(s.symbol);
        opts.push({ value: s.symbol, label: s.symbol });
      }
    }
    return opts.length > 0 ? opts : undefined;
  }, [sourcesData]);

  const allTasks: EvolutionTask[] = tasksData?.items ?? [];

  // Find active task (first running/pending)
  const activeTask = useMemo(
    () => allTasks.find((t) => isActiveStatus(t.status)) ?? null,
    [allTasks]
  );

  const activeTaskId = activeTask?.task_id ?? "";

  // Track last active task ID for chart data retention
  useEffect(() => {
    if (activeTaskId) setLastActiveTaskId(activeTaskId);
  }, [activeTaskId]);

  // Use lastActiveTaskId as fallback so chart keeps showing data after task completes
  const historyTaskId = activeTaskId || lastActiveTaskId;

  // Subscribe to WS updates for the active task
  useEvolutionWebSocket(activeTaskId);

  // Fetch detailed task data for the active task
  const { data: activeTaskDetail } = useQuery(
    useEvolutionTask(activeTaskId)
  );

  // Fetch history for score trend chart (uses lastActiveTaskId fallback)
  const { data: historyData } = useQuery(
    useEvolutionHistory(historyTaskId, !!activeTaskId)
  );

  const currentTask = activeTaskDetail ?? activeTask;

  // Discovered strategies from strategy table (auto-extracted during evolution)
  const { data: discoveredData } = useQuery(
    useDiscoveredStrategies(historyTaskId)
  );
  const effectiveStrategies = discoveredData ?? [];

  // Historical tasks (completed or stopped)
  const historicalTasks = useMemo(
    () =>
      allTasks.filter(
        (t) => t.status === "completed" || t.status === "stopped"
      ),
    [allTasks]
  );

  // --- Auto-expand config when task completes or no task ---
  useEffect(() => {
    if (!currentTask || !isActiveStatus(currentTask.status)) {
      if (configCollapsed) {
        setConfigCollapsed(false);
      }
    } else if (currentTask && isActiveStatus(currentTask.status) && !configCollapsed) {
      setConfigCollapsed(true);
    }
  }, [currentTask, configCollapsed]);

  // --- Mutations ---
  const createTask = useCreateEvolutionTask();
  const pauseTask = usePauseEvolutionTask();
  const stopTask = useStopEvolutionTask();
  const resumeTask = useResumeEvolutionTask();
  const saveStrategyMutation = useCreateStrategy();

  // --- Handlers ---
  const handleStartAuto = useCallback(
    async (config: {
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
    }) => {
      if (activeTask) {
        toast.error("已有正在进行的探索任务，请先停止后再创建新任务");
        return;
      }
      try {
        await createTask.mutateAsync({
          symbol: config.symbol,
          timeframe: config.timeframePool[0] ?? "4h",
          population_size: config.populationSize,
          max_generations: config.maxGenerations,
          indicator_pool: config.indicatorPool,
          timeframe_pool: config.timeframePool,
          mode: "auto",
          leverage: config.leverage,
          direction: config.direction,
          data_start: config.dataStart,
          data_end: config.dataEnd,
          requirements: config.requirements,
        });
        setConfigCollapsed(true);
      } catch {
        // handled by mutation onError
      }
    },
    [activeTask, createTask]
  );

  const handleStartSeed = useCallback(
    async (config: {
      symbol: string;
      initialDna: DNA;
      populationSize: number;
      maxGenerations: number;
      leverage: number;
      direction: "long" | "short" | "mixed";
      requirements?: RequirementsConfig;
    }) => {
      if (activeTask) {
        toast.error("已有正在进行的探索任务，请先停止后再创建新任务");
        return;
      }
      try {
        await createTask.mutateAsync({
          symbol: config.symbol,
          timeframe: config.initialDna.execution_genes.timeframe,
          population_size: config.populationSize,
          max_generations: config.maxGenerations,
          initial_dna: config.initialDna,
          mode: "seed",
          leverage: config.leverage,
          direction: config.direction,
          requirements: config.requirements,
        });
        setConfigCollapsed(true);
        setSeedDna(null);
      } catch {
        // handled by mutation onError
      }
    },
    [activeTask, createTask]
  );

  const handlePause = useCallback(() => {
    if (!activeTaskId) return;
    pauseTask.mutate(activeTaskId);
  }, [activeTaskId, pauseTask]);

  const handleResume = useCallback(() => {
    if (!activeTaskId) return;
    resumeTask.mutate(activeTaskId);
  }, [activeTaskId, resumeTask]);

  const handleStopConfirm = useCallback(() => {
    if (!stopTarget) return;
    stopTask.mutate(stopTarget.task_id, {
      onSuccess: () => setStopTarget(null),
    });
  }, [stopTarget, stopTask]);

  const handleSeedEvolve = useCallback(
    (dna: DNA) => {
      setSeedDna(dna);
      setSelectedMode("seed");
      setConfigCollapsed(false);
      configRef.current?.scrollIntoView({ behavior: "smooth" });
    },
    []
  );

  const handlePresetSelect = useCallback(
    (preset: Preset) => {
      setSelectedMode("auto");
      setConfigCollapsed(false);
      setPresetToApply(preset);
      configRef.current?.scrollIntoView({ behavior: "smooth" });
      toast.info(`已应用预设「${preset.title}」, 可调整参数后开始探索`);
    },
    []
  );

  const handleToggleExpand = useCallback((taskId: string) => {
    setExpandedStrategyId((prev) => (prev === taskId ? null : taskId));
  }, []);

  const handleViewTask = useCallback(
    (taskId: string) => {
      setDetailTaskId(taskId);
    },
    []
  );

  const handleVisualVerify = useCallback(
    (dna: DNA, task: EvolutionTask) => {
      navigate("/lab", {
        state: {
          dna,
          symbol: task.symbol,
          timeframe: task.timeframe,
          dataStart: task.data_start || task.data_time_start?.slice(0, 10) || undefined,
          dataEnd: task.data_end || task.data_time_end?.slice(0, 10) || undefined,
        },
      });
    },
    [navigate]
  );

  const handleSaveStrategy = useCallback(
    (strategy: DiscoveredStrategy) => {
      if (!strategy.dna) return;
      saveStrategyMutation.mutateAsync({
        name: strategy.name || `${strategy.symbol} ${strategy.timeframe} 进化策略`,
        dna: strategy.dna,
        symbol: strategy.symbol,
        timeframe: strategy.timeframe,
        source: "evolution",
        tags: "evolution,discovered",
        source_task_id: strategy.source_task_id,
      });
    },
    [saveStrategyMutation]
  );

  const handleStrategyBacktest = useCallback(
    (strategy: DiscoveredStrategy) => {
      if (!strategy.dna) return;
      const task = currentTask;
      navigate("/lab", {
        state: {
          dna: strategy.dna,
          symbol: strategy.symbol,
          timeframe: strategy.timeframe,
          dataStart: task?.data_start || task?.data_time_start?.slice(0, 10) || undefined,
          dataEnd: task?.data_end || task?.data_time_end?.slice(0, 10) || undefined,
        },
      });
    },
    [navigate, currentTask]
  );

  const handleNewExploration = useCallback(() => {
    setConfigCollapsed(false);
    setSeedDna(null);
    configRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // --- Loading skeleton ---
  if (tasksLoading) {
    return (
      <PageTransition>
        <div className="flex flex-col gap-4">
          <Skeleton className="h-56 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-60 rounded-xl" />
        </div>
      </PageTransition>
    );
  }

  // --- Derived data ---
  const historyRecords = historyData?.records ?? [];
  const targetScore = currentTask?.target_score ?? 80;

  return (
    <PageTransition>
      <div className="flex flex-col gap-4">
        {/* ================================================================== */}
        {/* Area 1 - Exploration Config                                       */}
        {/* ================================================================== */}
        <div ref={configRef}>
          <GlassCard className="p-5" hover={false}>
            {configCollapsed && currentTask ? (
              /* Collapsed summary */
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 text-xs text-slate-400">
                  <span className="font-medium text-slate-200">
                    {currentTask.symbol}
                  </span>
                  <span>
                    {formatTimeframeDisplay(currentTask)}
                  </span>
                  <span>
                    {currentTask.mode === "seed" ? "种子探索" : "自动探索"}
                  </span>
                  <span>
                    {SCORE_TEMPLATE_LABELS[currentTask.score_template] ?? currentTask.score_template}
                  </span>
                  {isActiveStatus(currentTask.status) && (
                    <span className="text-amber-400">
                      进化中 Gen {currentTask.current_generation}/
                      {currentTask.max_generations}
                    </span>
                  )}
                  {currentTask.best_fitness != null && (
                    <span className="text-emerald-400">
                      适应度 {currentTask.best_fitness.toFixed(3)}
                    </span>
                  )}
                  {currentTask.best_score != null && (
                    <span className="text-slate-500">
                      得分 {currentTask.best_score.toFixed(1)}
                    </span>
                  )}
                  {currentTask.qualified_count != null && currentTask.qualified_count > 0 && (
                    <span className="text-sky-400">
                      {currentTask.qualified_count} 达标
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  className="text-xs text-slate-500 hover:text-slate-400"
                  onClick={handleNewExploration}
                >
                  展开配置
                </button>
              </div>
            ) : (
              /* Full config form */
              <div className="flex flex-col gap-4">
                <h2 className="text-sm font-medium text-slate-400">
                  探索配置
                </h2>

                <SegmentedControl
                  value={selectedMode}
                  onChange={setSelectedMode}
                />

                {selectedMode === "auto" ? (
                  <AutoConfigForm
                    disabled={!!activeTask}
                    isPending={createTask.isPending}
                    onSubmit={handleStartAuto}
                    symbolOptions={dynamicSymbolOptions}
                    availableSources={sourcesData?.sources}
                    initialPreset={presetToApply}
                    onPresetApplied={() => setPresetToApply(null)}
                  />
                ) : (
                  <SeedConfigForm
                    disabled={!!activeTask}
                    isPending={createTask.isPending}
                    onSubmit={handleStartSeed}
                    seedDna={seedDna}
                    symbolOptions={dynamicSymbolOptions}
                  />
                )}
              </div>
            )}
          </GlassCard>
        </div>

        {/* ================================================================== */}
        {/* Area 2 - Exploration Progress                                      */}
        {/* ================================================================== */}
        <GlassCard className="p-5" hover={false}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Dna
                className={
                  currentTask
                    ? "h-4 w-4 text-amber-400"
                    : "h-4 w-4 text-slate-600"
                }
              />
              <h3
                className={
                  currentTask
                    ? "text-sm font-medium text-slate-200"
                    : "text-sm font-medium text-slate-600"
                }
              >
                探索进度
              </h3>
            </div>
            {currentTask && (
              <span className="font-mono text-xs text-slate-500">
                {currentTask.symbol} /{" "}
                {formatTimeframeDisplay(currentTask)}
              </span>
            )}
          </div>

          {currentTask ? (
            <div className="mt-4 flex flex-col gap-4">
              <ProgressPanel
                task={currentTask}
                onPause={handlePause}
                onResume={handleResume}
                onStop={() => setStopTarget(currentTask)}
                pausePending={pauseTask.isPending}
                stopPending={stopTask.isPending}
                resumePending={resumeTask.isPending}
              />
              <ScoreTrendChart
                records={historyRecords}
                targetScore={targetScore}
              />
            </div>
          ) : (
            <div className="mt-4 flex flex-col items-center gap-3 py-6">
              <Dna className="h-12 w-12 text-slate-700" />
              <p className="text-xs text-slate-500">
                没有正在进行的探索任务
              </p>
              <p className="text-[11px] text-slate-600">
                在上方配置参数, 开始你的策略探索
              </p>
              <div className="mt-2 w-full">
                <QuickPresets onSelect={handlePresetSelect} />
              </div>
            </div>
          )}
        </GlassCard>

        {/* ================================================================== */}
        {/* Area 3 - Discovered Strategies                                     */}
        {/* ================================================================== */}
        {effectiveStrategies.length > 0 && (
          <div ref={resultsRef}>
            <GlassCard className="p-5" hover={false}>
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-slate-400">
                  发现策略{" "}
                  <span className="font-mono text-slate-600">
                    ({effectiveStrategies.length})
                  </span>
                </h3>
              </div>
              <div className="mt-3">
                <StrategyList
                  strategies={effectiveStrategies}
                  expandedId={expandedStrategyId}
                  onToggleExpand={handleToggleExpand}
                  onSeedEvolve={handleSeedEvolve}
                  onSave={handleSaveStrategy}
                  onVisualVerify={handleStrategyBacktest}
                />
              </div>
            </GlassCard>
          </div>
        )}

        {/* ================================================================== */}
        {/* Area 5 - History                                                   */}
        {/* ================================================================== */}
        {historicalTasks.length > 0 && (
          <GlassCard className="p-5" hover={false}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-slate-400">
                历史探索
              </h3>
            </div>
            <div className="mt-3">
              <HistoryTable
                tasks={historicalTasks}
                onViewTask={handleViewTask}
                onSeedEvolve={handleSeedEvolve}
              />
            </div>
          </GlassCard>
        )}

        {/* No tasks at all - show empty state */}
        {allTasks.length === 0 && !currentTask && (
          <GlassCard className="p-5" hover={false}>
            <EmptyState
              icon={Dna}
              title="还没有探索记录"
              description="选择一种探索模式，开启你的策略进化之旅。系统将自动寻找最优策略组合。"
            />
          </GlassCard>
        )}

        {/* Stop confirmation dialog */}
        <ConfirmDialog
          open={stopTarget !== null}
          onOpenChange={(open) => {
            if (!open) setStopTarget(null);
          }}
          title="停止探索任务"
          description={`确定要停止探索任务「${stopTarget?.task_id.slice(0, 8) ?? ""}」吗？已探索的进度将被保留，已发现的有效策略仍可查看。`}
          confirmLabel="停止"
          variant="destructive"
          onConfirm={handleStopConfirm}
          loading={stopTask.isPending}
        />

        {/* Task detail drawer */}
        {detailTaskId && (
          <TaskDetailDrawer
            taskId={detailTaskId}
            open={!!detailTaskId}
            onClose={() => setDetailTaskId(null)}
            onSeedEvolve={(dna) => {
              setDetailTaskId(null);
              handleSeedEvolve(dna);
            }}
            onVisualVerify={(dna, task) => {
              handleVisualVerify(dna, task);
            }}
          />
        )}
      </div>
    </PageTransition>
  );
}
