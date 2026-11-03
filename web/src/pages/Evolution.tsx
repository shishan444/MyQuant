/**
 * 05 进化中心·主页面（fe-research-execution-domain 3.1 布局还原）。
 * 路由 /evolution。原型 05 画框形制：顶部条（进化中任务名+暂停/停止
 * +指标行）→上行三列（左=任务参数〔原型 B2 表单位，alignment-batch D3
 * 只读折中——发起归 MCP trigger_evolution〕/中=评分趋势〔CHART_VA〕
 * /右=发现策略+产出两源〔champion+snapshot〕）→历史任务表（B5）。
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
import { Skeleton } from "antd";

import { TaskRuntimePanel } from "@/components/evolution/TaskRuntimePanel";
import { EvolvedStrategySources } from "@/components/evolution/EvolvedStrategySources";
import { ScoreTrendChart } from "@/components/evolution/ScoreTrendChart";
import { StrategyList } from "@/components/evolution/StrategyList";
import { HistoryTable } from "@/components/evolution/HistoryTable";
import { TaskDetailDrawer } from "@/components/evolution/TaskDetailDrawer";
import { StopConfirmDialog } from "@/components/evolution/StopConfirmDialog";

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
        {/* 顶部条（原型 05-B3 进度头+进度注升格：任务名+指标行+暂停/停止）    */}
        {/* ================================================================== */}
        <GlassCard className="px-5 py-3" hover={false}>
          {currentTask ? (
            <TaskRuntimePanel
              task={currentTask}
              onPause={handlePause}
              onResume={handleResume}
              onStop={() => setStopTarget(currentTask)}
              pausePending={pauseTask.isPending}
              stopPending={stopTask.isPending}
              resumePending={resumeTask.isPending}
            />
          ) : (
            <div className="flex flex-wrap items-center gap-3 py-1.5">
              <Dna className="h-4 w-4 text-text-muted" />
              <p className="text-xs text-text-secondary">当前没有进化任务</p>
              <p className="text-[11px] text-text-muted">
                任务发起经 MCP trigger_evolution，本页回显任务与产出
              </p>
            </div>
          )}
        </GlassCard>

        {/* ================================================================== */}
        {/* 上行三列（原型 05-B2 参数 / B3 趋势 / B4 发现策略）               */}
        {/* ================================================================== */}
        <div className="grid gap-4 lg:grid-cols-[300px_1fr_380px]">
          {/* 左列 - 任务参数（原型 B2 表单位：发起归 MCP，D3 只读折中回显） */}
          <GlassCard className="p-4" hover={false}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-text-secondary">任务参数</h3>
              {currentTask && (
                <span className="font-mono text-[11px] text-text-muted">
                  {currentTask.symbol} / {formatTimeframeDisplay(currentTask)}
                </span>
              )}
            </div>
            {currentTask ? (
              <TaskParamsSummary task={currentTask} />
            ) : (
              <p className="mt-4 text-xs text-text-muted">
                发起任务后此处回显参数（品种/种群/代数/指标池等）
              </p>
            )}
            <p className="mt-3 border-t border-border-faint pt-2 text-[10px] text-text-muted">
              只读回显 · 任务发起经 MCP trigger_evolution
            </p>
          </GlassCard>

          {/* 中列 - 评分趋势（原型 B3 趋势图，CHART_VA 容器） */}
          <GlassCard className="p-4" hover={false}>
            <h3 className="text-sm font-medium text-text-secondary">
              评分趋势 · 历代最优
            </h3>
            <div className="mt-3">
              <ScoreTrendChart records={historyRecords} targetScore={targetScore} />
            </div>
          </GlassCard>

          {/* 右列 - 发现策略+产出两源（原型 B4 前沿个体） */}
          <div className="flex min-w-0 flex-col gap-4">
            <GlassCard className="p-4" hover={false}>
              <h3 className="text-sm font-medium text-text-secondary">
                发现策略 · 前沿个体{" "}
                <span className="font-mono text-[11px] text-text-muted">
                  ({effectiveStrategies.length})
                </span>
              </h3>
              <div className="mt-3">
                {effectiveStrategies.length > 0 ? (
                  <StrategyList
                    strategies={effectiveStrategies}
                    expandedId={expandedStrategyId}
                    onToggleExpand={handleToggleExpand}
                    onSeedEvolve={handleSeedEvolve}
                    onSave={handleSaveStrategy}
                    onVisualVerify={handleStrategyBacktest}
                  />
                ) : (
                  <p className="py-4 text-xs text-text-muted">
                    暂无发现策略——进化过程摘取的有效个体将在此呈现
                  </p>
                )}
              </div>
            </GlassCard>

            {!!historyTaskId && (
              <GlassCard className="p-4" hover={false} data-testid="evolved-sources-card">
                <h3 className="text-sm font-medium text-text-secondary">
                  产出策略（冠军与代际快照）
                </h3>
                <div className="mt-3">
                  <EvolvedStrategySources
                    strategies={taskStrategiesData?.strategies ?? []}
                    loading={taskStrategiesLoading}
                    error={taskStrategiesError ? (taskStrategiesError as Error).message : null}
                  />
                </div>
              </GlassCard>
            )}
          </div>
        </div>

        {/* ================================================================== */}
        {/* 历史任务表（原型 05-B5，常驻）                                     */}
        {/* ================================================================== */}
        <GlassCard className="p-4" hover={false}>
          <h3 className="text-sm font-medium text-text-secondary">历史任务</h3>
          <div className="mt-3">
            {historicalTasks.length > 0 ? (
              <HistoryTable
                tasks={historicalTasks}
                onViewTask={handleViewTask}
                onSeedEvolve={handleSeedEvolve}
              />
            ) : (
              <p className="py-2 text-xs text-text-muted">
                暂无历史任务——完成的任务将在此呈现
              </p>
            )}
          </div>
        </GlassCard>

        {/* 停止确认对话框（07-M1 规格：标题问句+事实三句+双钮） */}
        <StopConfirmDialog
          open={stopTarget !== null}
          onOpenChange={(open) => {
            if (!open) setStopTarget(null);
          }}
          taskId={stopTarget?.task_id ?? null}
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
