import { useState } from "react";
import { Pause, Square, Play, AlertTriangle, CheckCircle, Database, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { Button as AntButton, Progress as AntProgress } from "antd";
import { StatCard } from "@/components/StatCard";
import { cn, formatNumber } from "@/lib/utils";
import { STATUS_LABELS as STATUS_LABEL, isActiveStatus } from "@/lib/constants";
import type { EvolutionTask } from "@/types/api";

const PHASE_LABELS: Record<string, string> = {
  initializing: "初始化中",
  data_loading: "加载数据",
  evolution_running: "进化运行中",
};

interface ProgressPanelProps {
  task: EvolutionTask;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  pausePending: boolean;
  stopPending: boolean;
  resumePending: boolean;
}

export function ProgressPanel({
  task,
  onPause,
  onResume,
  onStop,
  pausePending,
  stopPending,
  resumePending,
}: ProgressPanelProps) {
  const currentGeneration = task.current_generation ?? 0;
  const maxGenerations = task.max_generations ?? 1;
  const progressPercent =
    maxGenerations > 0
      ? Math.round((currentGeneration / maxGenerations) * 100)
      : 0;
  const bestScore = task.best_score ?? 0;
  const bestFitness = task.best_fitness ?? 0;
  const attemptedCount = currentGeneration * (task.population_size ?? 15);
  const isActive = isActiveStatus(task.status);
  const isPaused = task.status === "paused";
  const reachedTarget = (task.qualified_count ?? 0) > 0;
  const isContinuous = task.continuous === true;
  const [showChampion, setShowChampion] = useState(false);
  const phaseLabel = task.current_phase ? PHASE_LABELS[task.current_phase] ?? task.current_phase : null;
  const showPhaseHint = isActive && currentGeneration === 0 && phaseLabel;

  return (
    <div className="flex flex-col gap-4">
      {/* Status bar */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              task.status === "running" && "bg-blue-400 animate-pulse",
              task.status === "pending" && "bg-slate-500 animate-pulse",
              task.status === "paused" && "bg-amber-400",
              task.status === "stopped" && "bg-slate-500",
              task.status === "completed" && "bg-emerald-400"
            )}
          />
          <span className="text-xs font-medium text-slate-100">
            {STATUS_LABEL[task.status]}
          </span>
        </div>
        <span className="text-xs text-slate-500">
          已探索:{" "}
          <span className="font-mono text-slate-400">
            {attemptedCount.toLocaleString()}
          </span>{" "}
          组
        </span>
        <span className="text-xs text-slate-500">
          第 <span className="font-mono text-slate-400">{currentGeneration}</span>{" "}
          代{isContinuous ? "" : ` / ${maxGenerations} 代`}
        </span>
      </div>

      {/* Phase hint: show meaningful progress when generation=0 */}
      {showPhaseHint && (
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Loader2 className="h-3 w-3 animate-spin text-blue-400" />
          <span>{phaseLabel}</span>
        </div>
      )}

      {/* Progress bar - hidden for continuous mode (no upper bound) */}
      {!isContinuous && (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500">
            进度
          </span>
          <span className="font-mono text-slate-400">
            {progressPercent}%
          </span>
        </div>
        <AntProgress
          percent={progressPercent}
          showInfo={false}
          size={{ height: 8 }}
          strokeColor={reachedTarget ? { from: "#00b37a", to: "#d9a514" } : { from: "#3b82f6", to: "#8B5CF6" }}
        />
      </div>
      )}

      {/* Score comparison */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          label="当前适应度"
          value={bestFitness.toFixed(2)}
          trend={reachedTarget ? "up" : "neutral"}
        />
        <StatCard
          label="综合得分"
          value={formatNumber(bestScore)}
          trend="neutral"
        />
      </div>
      {/* Qualified indicator */}
      {reachedTarget && (
        <div className="flex items-center gap-2">
          <CheckCircle className="h-3.5 w-3.5 text-emerald-400" />
          <span className="text-xs font-medium text-emerald-400">已达标</span>
          {task.qualified_count != null && task.qualified_count > 0 && (
            <span className="text-[11px] text-slate-500">
              达标策略: <span className="font-mono text-emerald-400">{task.qualified_count}</span>
            </span>
          )}
        </div>
      )}

      {/* Diagnostics tags */}
      <div className="flex items-center gap-2">
        {task.data_row_count != null && task.data_row_count > 0 ? (
          <div className="flex items-center gap-1 text-[11px] text-emerald-400">
            <Database className="h-3 w-3" />
            <span>{task.data_row_count.toLocaleString()} 条K线</span>
          </div>
        ) : (
          <div className="flex items-center gap-1 text-[11px] text-amber-500">
            <AlertTriangle className="h-3 w-3" />
            <span>数据范围未确认</span>
          </div>
        )}
        {task.leverage > 1 && (
          <div className="flex items-center gap-1 text-[11px] text-amber-400">
            <span>{task.leverage}x</span>
          </div>
        )}
        <div className={cn(
          "flex items-center gap-1 text-[11px]",
          task.direction === "short"
            ? "text-red-400"
            : task.direction === "mixed"
              ? "text-purple-400"
              : "text-emerald-400"
        )}>
          <CheckCircle className="h-3 w-3" />
          <span>{task.direction === "short" ? "做空" : task.direction === "mixed" ? "混合" : "做多"}</span>
        </div>
      </div>

      {/* Champion preview (from WS real-time updates) */}
      {task.champion_dna && (
        <div className="rounded-lg border border-slate-700/30 bg-white/[0.01]">
          <button
            type="button"
            className="flex w-full items-center justify-between px-3 py-2 text-xs text-slate-400 hover:text-slate-300"
            onClick={() => setShowChampion(!showChampion)}
          >
            <span>当前 Champion</span>
            {showChampion ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
          {showChampion && (
            <div className="border-t border-slate-700/30 px-3 py-2">
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span>
                  {(() => {
                    const d = task.champion_dna.risk_genes?.direction ?? "long";
                    return d === "short" ? "做空" : d === "mixed" ? "混合" : "做多";
                  })()}
                </span>
                <span>
                  止损 {((task.champion_dna.risk_genes?.stop_loss ?? 0.03) * 100).toFixed(1)}%
                </span>
                <span>
                  止盈 {((task.champion_dna.risk_genes?.take_profit ?? 0.06) * 100).toFixed(1)}%
                </span>
                {(task.champion_dna.risk_genes?.leverage ?? 1) > 1 && (
                  <span className="text-amber-400">
                    {task.champion_dna.risk_genes?.leverage}x
                  </span>
                )}
              </div>
              <div className="mt-1 text-[11px] text-slate-600">
                信号: {(task.champion_dna.signal_genes?.length ?? 0) + (task.champion_dna.layers?.length ?? 0)} 个基因
              </div>
              {task.champion_metrics && (
                <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
                  <span className="text-slate-500">
                    年化 <span className={task.champion_metrics.annual_return > 0 ? "text-emerald-400" : "text-red-400"}>
                      {(task.champion_metrics.annual_return * 100).toFixed(1)}%
                    </span>
                  </span>
                  <span className="text-slate-500">
                    夏普 <span className={task.champion_metrics.sharpe_ratio > 0 ? "text-emerald-400" : "text-red-400"}>
                      {task.champion_metrics.sharpe_ratio.toFixed(2)}
                    </span>
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Control buttons */}
      {(isActive || isPaused) && (
        <div className="flex items-center gap-2">
          {isPaused ? (
            <AntButton
              size="small"
             
              className="gap-1.5"
              onClick={onResume}
              disabled={resumePending}
            >
              <Play className="h-3.5 w-3.5" />
              恢复
            </AntButton>
          ) : (
            <AntButton
              size="small"
             
              className="gap-1.5"
              onClick={onPause}
              disabled={pausePending}
            >
              <Pause className="h-3.5 w-3.5" />
              暂停
            </AntButton>
          )}
          <AntButton
            size="small"
           
            className="gap-1.5 border-red-400/30 text-red-400 hover:bg-red-400/10"
            onClick={onStop}
            disabled={stopPending}
          >
            <Square className="h-3.5 w-3.5" />
            停止
          </AntButton>
        </div>
      )}
    </div>
  );
}
