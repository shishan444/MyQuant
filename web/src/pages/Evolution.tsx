/**
 * 05 进化中心·主页面（alignment-batch D3 只读折中）。路由 /evolution，
 * 呈现任务参数回显/探索进度/发现策略/产出两源（champion+snapshot）/
 * 历史记录——任务发起归 MCP trigger_evolution，本页只回显不发起点。
 * 数据经 react-query 拉取+活动任务 WS 实时更新；图表数据以
 * lastActiveTaskId 兜底，任务完成后仍保留。消费方：App 路由
 * （用户经侧边导航进入的页面入口）。
 */
import { useState, useCallback, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useLocation } from "react-router";
import { Dna } from "lucide-react";
import { toast } from "sonner";

import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { EmptyState } from "@/components/EmptyState";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Skeleton } from "antd";

import { TaskRuntimePanel } from "@/components/evolution/TaskRuntimePanel";
import { EvolvedStrategySources } from "@/components/evolution/EvolvedStrategySources";
import { ScoreTrendChart } from "@/components/evolution/ScoreTrendChart";
import { StrategyList } from "@/components/evolution/StrategyList";
import { HistoryTable } from "@/components/evolution/HistoryTable";
import { TaskDetailDrawer } from "@/components/evolution/TaskDetailDrawer";

import {
  useEvolutionTasks,
  useEvolutionTask,
  useEvolutionHistory,
  useStopEvolutionTask,
  usePauseEvolutionTask,
  useResumeEvolutionTask,
  useEvolutionWebSocket,
  useDiscoveredStrategies,
} from "@/hooks/useEvolution";
import { getTaskStrategies } from "@/services/evolution";
import { useCreateStrategy } from "@/hooks/useStrategies";
import { isActiveStatus, SCORE_TEMPLATE_LABELS } from "@/lib/constants";
import type { EvolutionTask, DNA, DiscoveredStrategy } from "@/types/api";

// ---------------------------------------------------------------------------
// 主页面组件
// ---------------------------------------------------------------------------

function formatTimeframeDisplay(task: EvolutionTask): string {
  return task.timeframe_pool && task.timeframe_pool.length > 1
    ? task.timeframe_pool.join("+").toUpperCase()
    : task.timeframe.toUpperCase();
}

export function Evolution() {
  const navigate = useNavigate();
  const location = useLocation();

  // --- 展开的策略 ID（策略卡展开态） ---
  const [expandedStrategyId, setExpandedStrategyId] = useState<string | null>(null);

  // --- 停止确认对话框（待停任务） ---
  const [stopTarget, setStopTarget] = useState<EvolutionTask | null>(null);

  // --- 最近活动任务 ID（任务完成后图表数据据此保留） ---
  const [lastActiveTaskId, setLastActiveTaskId] = useState<string>("");

  // --- 任务详情抽屉 ---
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);

  // --- 数据获取 ---
  const { data: tasksData, isLoading: tasksLoading } = useQuery(
    useEvolutionTasks({ limit: 20 })
  );

  const allTasks: EvolutionTask[] = tasksData?.items ?? [];

  // 找活动任务（首个 running/pending）
  const activeTask = useMemo(
    () => allTasks.find((t) => isActiveStatus(t.status)) ?? null,
    [allTasks]
  );

  const activeTaskId = activeTask?.task_id ?? "";

  // 记录最近活动任务 ID，供图表数据保留
  useEffect(() => {
    if (activeTaskId) setLastActiveTaskId(activeTaskId);
  }, [activeTaskId]);

  // 以 lastActiveTaskId 兜底——任务完成后图表继续显示数据
  const historyTaskId = activeTaskId || lastActiveTaskId;

  // 订阅活动任务的 WS 更新
  useEvolutionWebSocket(activeTaskId);

  // 拉取活动任务详情
  const { data: activeTaskDetail } = useQuery(
    useEvolutionTask(activeTaskId)
  );

  // 拉取评分趋势图历史（lastActiveTaskId 兜底）
  const { data: historyData } = useQuery(
    useEvolutionHistory(historyTaskId, !!activeTaskId)
  );

  // 产出策略两源（D3）：champion（任务表持久冠军）+ snapshot（代际 Top 快照）
  const {
    data: taskStrategiesData,
    isLoading: taskStrategiesLoading,
    error: taskStrategiesError,
  } = useQuery({
    queryKey: ["evolution", "task-strategies", historyTaskId],
    queryFn: () => getTaskStrategies(historyTaskId),
    enabled: !!historyTaskId,
  });

  const currentTask = activeTaskDetail ?? activeTask;

  // 发现的策略（来自策略表，进化过程自动摘取）
  const { data: discoveredData } = useQuery(
    useDiscoveredStrategies(historyTaskId)
  );
  const effectiveStrategies = discoveredData ?? [];

  // 历史任务（completed 或 stopped）
  const historicalTasks = useMemo(
    () =>
      allTasks.filter(
        (t) => t.status === "completed" || t.status === "stopped"
      ),
    [allTasks]
  );

  // --- 路由 seed 注入提示（发起归 MCP 后，seed 入口仅提示） ---
  useEffect(() => {
    const state = location.state as { seedDna?: DNA } | null;
    if (state?.seedDna) {
      window.history.replaceState({}, "");
      toast.info("进化任务发起已迁移至 MCP trigger_evolution，本页回显任务与产出");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- 变更操作（mutations） ---
  const pauseTask = usePauseEvolutionTask();
  const stopTask = useStopEvolutionTask();
  const resumeTask = useResumeEvolutionTask();
  const saveStrategyMutation = useCreateStrategy();

  // --- 事件处理 ---
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

  // 发起归 MCP trigger_evolution（D3 只读折中）——种子入口保留但只做渠道提示
  const handleSeedEvolve = useCallback(() => {
    toast.info("进化任务发起已迁移至 MCP trigger_evolution，本页回显任务与产出");
  }, []);

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

  // --- 加载骨架屏 ---
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

  // --- 派生数据 ---
  const historyRecords = historyData?.records ?? [];
  const targetScore = currentTask?.target_score ?? 80;

  return (
    <PageTransition>
      <div className="flex flex-col gap-4">
        {/* ================================================================== */}
        {/* Area 1 - 任务参数（只读回显，发起归 MCP trigger_evolution）        */}
        {/* ================================================================== */}
        <div>
          <GlassCard className="p-5" hover={false}>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-slate-400">任务参数</h2>
              <span className="text-[11px] text-slate-600">
                只读回显 · 任务发起经 MCP trigger_evolution
              </span>
            </div>

            {currentTask ? (
              <TaskParamsSummary task={currentTask} />
            ) : (
              <div className="mt-4 flex flex-col items-center gap-3 py-6">
                <Dna className="h-10 w-10 text-slate-700" />
                <p className="text-xs text-slate-500">当前没有进化任务</p>
                <p className="text-[11px] text-slate-600 text-center">
                  任务发起已迁移至 MCP trigger_evolution，本页回显任务参数、运行进度与产出策略
                </p>
              </div>
            )}
          </GlassCard>
        </div>

        {/* ================================================================== */}
        {/* Area 2 - 探索进度                                                 */}
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
              <TaskRuntimePanel
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
                任务发起后此处呈现运行进度
              </p>
            </div>
          )}
        </GlassCard>

        {/* ================================================================== */}
        {/* Area 3 - 发现的策略                                               */}
        {/* ================================================================== */}
        {effectiveStrategies.length > 0 && (
          <div>
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
        {/* Area 3.5 - 产出策略两源（champion / snapshot）                     */}
        {/* ================================================================== */}
        {!!historyTaskId && (
          <GlassCard className="p-5" hover={false} data-testid="evolved-sources-card">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-slate-400">
                产出策略（冠军与代际快照）
              </h3>
            </div>
            <div className="mt-3">
              <EvolvedStrategySources
                strategies={taskStrategiesData?.strategies ?? []}
                loading={taskStrategiesLoading}
                error={taskStrategiesError ? (taskStrategiesError as Error).message : null}
              />
            </div>
          </GlassCard>
        )}

        {/* ================================================================== */}
        {/* Area 5 - 历史记录                                                 */}
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

        {/* 完全无任务 - 显示空状态 */}
        {allTasks.length === 0 && !currentTask && (
          <GlassCard className="p-5" hover={false}>
            <EmptyState
              icon={Dna}
              title="还没有进化任务"
              description="任务发起经 MCP trigger_evolution 调用；发起后本页回显任务参数、运行进度与产出策略。"
            />
          </GlassCard>
        )}

        {/* 停止确认对话框 */}
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

        {/* 任务详情抽屉 */}
        {detailTaskId && (
          <TaskDetailDrawer
            taskId={detailTaskId}
            open={!!detailTaskId}
            onClose={() => setDetailTaskId(null)}
            onSeedEvolve={() => {
              setDetailTaskId(null);
              handleSeedEvolve();
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

/* ── 任务参数只读回显（D3：发起归 MCP，页面只呈现任务参数） ── */

function TaskParamsSummary({ task }: { task: EvolutionTask }) {
  const directionLabel =
    task.direction === "short" ? "做空" : task.direction === "mixed" ? "混合" : "做多";
  const dataRange =
    task.data_start || task.data_end
      ? `${task.data_start ?? "?"} ~ ${task.data_end ?? "?"}`
      : "未指定";
  const actualRange =
    task.data_time_start || task.data_time_end
      ? `${(task.data_time_start ?? "?").slice(0, 10)} ~ ${(task.data_time_end ?? "?").slice(0, 10)}`
      : null;
  const pool = task.indicator_pool;

  const rows: Array<{ label: string; value: string; title?: string }> = [
    { label: "品种", value: task.symbol },
    { label: "周期", value: formatTimeframeDisplay(task) },
    { label: "模式", value: task.mode === "seed" ? "种子探索" : "自动探索" },
    {
      label: "评分模板",
      value: SCORE_TEMPLATE_LABELS[task.score_template] ?? task.score_template,
    },
    { label: "种群规模", value: `${task.population_size}` },
    {
      label: "最大代数",
      value: task.continuous === true ? "连续模式（不限）" : `${task.max_generations}`,
    },
    { label: "杠杆", value: `${task.leverage}x` },
    { label: "方向", value: directionLabel },
    { label: "数据范围", value: dataRange, title: actualRange ? `实际数据 ${actualRange}` : undefined },
    {
      label: "指标池",
      value: pool && pool.length > 0 ? `${pool.length} 项` : "未指定",
      title: pool && pool.length > 0 ? pool.join(", ") : undefined,
    },
    {
      label: "达标目标",
      value: task.requirements?.objective ?? "未指定",
    },
  ];

  return (
    <div
      className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-4"
      data-testid="task-params"
    >
      {rows.map((r) => (
        <div key={r.label} className="flex items-baseline gap-2 min-w-0" data-testid={`task-param-${r.label}`}>
          <span className="text-[11px] text-slate-500 shrink-0">{r.label}</span>
          <span className="text-xs font-mono text-slate-200 truncate" title={r.title ?? r.value}>
            {r.value}
          </span>
        </div>
      ))}
    </div>
  );
}
