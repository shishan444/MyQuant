/**
 * 05 进化中心·顶部条（fe-research-execution-domain 3.1，原型 05-B3 进度头
 * +进度注升格为页顶横条）。契约登记表五「无进度条」：进度呈现=已耗时
 * （created_at 起算）+状态灯（后端真实 status 值域五态映射颜色），
 * 不用 generation/max 折算百分条（alignment-batch D3 裁决沿袭）。
 * 控制钮文案带语义标注（原型同形）：暂停/恢复的恢复语义、停止的落库语义
 * 如实披露。纯函数与 hook 在 taskRuntimeUtils.ts。
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

/** 指标项（原型 05-B3 进度注形制：键+值并排） */
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-[10px] text-text-muted">{label}</span>
      <span className="font-num text-xs text-text-primary">{value}</span>
    </span>
  );
}

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
    <div
      className="flex flex-wrap items-center gap-x-5 gap-y-2"
      data-testid="task-runtime-panel"
    >
      {/* 任务名+状态灯（原型进度头：evo_xxxx · 运行中） */}
      <div className="flex items-center gap-1.5" data-testid="task-status-row">
        <span
          data-testid="task-status-light"
          data-status={task.status}
          className={cn(
            "inline-block h-2 w-2 rounded-full",
            STATUS_LIGHT[task.status] ?? "bg-slate-600"
          )}
        />
        <span className="font-mono text-xs text-text-primary">
          {task.task_id.slice(0, 8)}
        </span>
        <span className="text-xs text-text-secondary">
          {STATUS_LABEL[task.status]}
        </span>
      </div>

      {/* 指标行（原型进度注：代数/评分/适应度/已耗时） */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <Metric
          label="代数"
          value={isContinuous ? `${currentGeneration}` : `${currentGeneration} / ${maxGenerations}`}
        />
        <Metric label="当前最优评分" value={formatNumber(task.best_score ?? 0)} />
        <Metric label="当前适应度" value={(task.best_fitness ?? 0).toFixed(2)} />
        <span className="flex items-baseline gap-1.5 whitespace-nowrap">
          <span className="text-[10px] text-text-muted">已耗时</span>
          <span className="font-num text-xs text-text-secondary" data-testid="task-elapsed">
            {formatElapsed(elapsedSeconds)}
          </span>
        </span>
      </div>

      <span className="flex-1" />

      {/* 控制钮（运行控制非任务发起——发起归 MCP trigger_evolution；
          文案语义标注如实披露恢复=完整重跑、停止=当前代落库后退出） */}
      {(isActive || isPaused) && (
        <div className="flex items-center gap-2">
          {isPaused ? (
            <AntButton size="small" onClick={onResume} disabled={resumePending}>
              <Play className="h-3.5 w-3.5" /> 恢复（从 initial_dna 完整重跑）
            </AntButton>
          ) : (
            <AntButton size="small" onClick={onPause} disabled={pausePending}>
              <Pause className="h-3.5 w-3.5" /> 暂停
            </AntButton>
          )}
          <AntButton
            size="small"
            className="border-loss/30 text-loss hover:bg-loss/10"
            onClick={onStop}
            disabled={stopPending}
          >
            <Square className="h-3.5 w-3.5" /> 停止（当前代落库后退出）
          </AntButton>
        </div>
      )}
    </div>
  );
}
