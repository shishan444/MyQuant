/**
 * 05 进化中心·任务运行面板（alignment-batch D3）。
 * 契约登记表五「无进度条」：进度呈现=已耗时（created_at 起算）+状态灯
 * （后端真实 status 值域五态映射颜色），不用 generation/max 折算百分条。
 * 纯函数与 hook（formatElapsed/useElapsedSeconds）在 taskRuntimeUtils.ts
 * （react-refresh：组件文件只导出组件）。
 */
import { Pause, Square, Play } from "lucide-react";
import { Button as AntButton } from "antd";
import { cn, formatNumber } from "@/lib/utils";
import { STATUS_LABELS as STATUS_LABEL, isActiveStatus } from "@/lib/constants";
import { formatElapsed, useElapsedSeconds } from "./taskRuntimeUtils";
import type { EvolutionTask } from "@/types/api";

interface TaskRuntimePanelProps {
  task: EvolutionTask;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  pausePending: boolean;
  stopPending: boolean;
  resumePending: boolean;
}

/** 状态灯五态色 ← 后端 EvolutionTaskStatus 真实值域 */
const STATUS_LIGHT: Record<string, string> = {
  running: "bg-blue-400 animate-pulse",
  pending: "bg-slate-500 animate-pulse",
  paused: "bg-amber-400",
  stopped: "bg-slate-500",
  completed: "bg-emerald-400",
};

export function TaskRuntimePanel({
  task,
  onPause,
  onResume,
  onStop,
  pausePending,
  stopPending,
  resumePending,
}: TaskRuntimePanelProps) {
  const elapsedSeconds = useElapsedSeconds(task);
  const isActive = isActiveStatus(task.status);
  const isPaused = task.status === "paused";
  const currentGeneration = task.current_generation ?? 0;
  const maxGenerations = task.max_generations ?? 1;
  const isContinuous = task.continuous === true;

  return (
    <div className="flex flex-col gap-3" data-testid="task-runtime-panel">
      {/* 状态灯 + 已耗时（无进度条——进度不可线性折算） */}
      <div className="flex items-center gap-4" data-testid="task-status-row">
        <div className="flex items-center gap-1.5">
          <span
            data-testid="task-status-light"
            data-status={task.status}
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              STATUS_LIGHT[task.status] ?? "bg-slate-600"
            )}
          />
          <span className="text-xs font-medium text-slate-100">
            {STATUS_LABEL[task.status]}
          </span>
        </div>
        <span className="text-xs text-slate-500">
          已耗时{" "}
          <span className="font-mono text-slate-300" data-testid="task-elapsed">
            {formatElapsed(elapsedSeconds)}
          </span>
        </span>
        <span className="text-xs text-slate-500">
          第 <span className="font-mono text-slate-400">{currentGeneration}</span> 代
          {isContinuous ? "" : ` / ${maxGenerations} 代`}
        </span>
      </div>

      {/* 得分概要 */}
      <div className="grid grid-cols-2 gap-3">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[10px] text-slate-500">当前适应度</span>
          <span className="text-sm font-mono text-slate-200">
            {(task.best_fitness ?? 0).toFixed(2)}
          </span>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-[10px] text-slate-500">综合得分</span>
          <span className="text-sm font-mono text-slate-200">
            {formatNumber(task.best_score ?? 0)}
          </span>
        </div>
      </div>

      {/* 控制按钮（运行控制非任务发起——发起归 MCP trigger_evolution） */}
      {(isActive || isPaused) && (
        <div className="flex items-center gap-2">
          {isPaused ? (
            <AntButton size="small" className="gap-1.5" onClick={onResume} disabled={resumePending}>
              <Play className="h-3.5 w-3.5" /> 恢复
            </AntButton>
          ) : (
            <AntButton size="small" className="gap-1.5" onClick={onPause} disabled={pausePending}>
              <Pause className="h-3.5 w-3.5" /> 暂停
            </AntButton>
          )}
          <AntButton
            size="small"
            className="gap-1.5 border-red-400/30 text-red-400 hover:bg-red-400/10"
            onClick={onStop}
            disabled={stopPending}
          >
            <Square className="h-3.5 w-3.5" /> 停止
          </AntButton>
        </div>
      )}
    </div>
  );
}
