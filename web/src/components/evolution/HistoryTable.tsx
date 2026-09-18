import { useMemo, memo } from "react";
import { Eye, Dna } from "lucide-react";
import { Button as AntButton, Tag } from "antd";
import { cn, formatNumber } from "@/lib/utils";
import { TIMEFRAME_LABELS } from "@/lib/constants";
import type { EvolutionTask, DNA } from "@/types/api";

interface HistoryTableProps {
  tasks: EvolutionTask[];
  onViewTask: (taskId: string) => void;
  onSeedEvolve: (dna: DNA) => void;
}

export function HistoryTable({
  tasks,
  onViewTask,
  onSeedEvolve,
}: HistoryTableProps) {
  if (tasks.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-slate-700/30 text-slate-500">
            <th className="py-2 text-left font-medium">交易对/周期</th>
            <th className="py-2 text-center font-medium">方向</th>
            <th className="py-2 text-right font-medium">综合得分</th>
            <th className="py-2 text-right font-medium">适应度</th>
            <th className="py-2 text-center font-medium">产出</th>
            <th className="py-2 text-center font-medium">效率</th>
            <th className="py-2 text-center font-medium">代数</th>
            <th className="py-2 text-center font-medium">状态</th>
            <th className="py-2 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <HistoryRow
              key={task.task_id}
              task={task}
              onView={() => onViewTask(task.task_id)}
              onSeedEvolve={() =>
                task.champion_dna &&
                onSeedEvolve(task.champion_dna)
              }
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface HistoryRowProps {
  task: EvolutionTask;
  onView: () => void;
  onSeedEvolve: () => void;
}

const HistoryRow = memo(function HistoryRow({ task, onView, onSeedEvolve }: HistoryRowProps) {
  const pairDisplay = useMemo(() => {
    const sym = task.symbol.replace("USDT", "");
    if (task.timeframe_pool && task.timeframe_pool.length > 1) {
      const tfs = task.timeframe_pool
        .map((tf) => TIMEFRAME_LABELS[tf] ?? tf)
        .join("+");
      return `${sym} ${tfs}`;
    }
    return `${sym} ${TIMEFRAME_LABELS[task.timeframe] ?? task.timeframe.toUpperCase()}`;
  }, [task.symbol, task.timeframe, task.timeframe_pool]);

  const isCompleted = task.status === "completed";
  const isStopped = task.status === "stopped";
  const strategyCount = task.strategy_count ?? 0;
  const efficiency = task.exploration_efficiency ?? 0;

  const directionLabel = task.direction === "short" ? "做空" : task.direction === "mixed" ? "混合" : "做多";
  const directionColor = task.direction === "short"
    ? "border-red-400/30 text-red-400"
    : task.direction === "mixed"
      ? "border-purple-400/30 text-purple-400"
      : "border-emerald-400/30 text-emerald-400";

  return (
    <tr className="group border-b border-slate-700/10 transition-colors hover:bg-white/[0.02]">
      <td className="py-3 text-slate-200">{pairDisplay}</td>
      <td className="py-3 text-center">
        <Tag className={cn("!m-0 !text-[10px]", directionColor)}>
          {directionLabel}
        </Tag>
      </td>
      <td className="py-3 text-right font-mono font-semibold text-slate-200">
        {formatNumber(task.best_score ?? 0)}
      </td>
      <td className="py-3 text-right font-mono text-slate-300">
        {task.best_fitness != null
          ? task.best_fitness.toFixed(2)
          : "-"}
      </td>
      <td className="py-3 text-center">
        {strategyCount > 0 ? (
          <Tag color="success" className="!m-0 !text-[10px]">
            {strategyCount}
          </Tag>
        ) : (
          <span className="text-slate-600">0</span>
        )}
      </td>
      <td className="py-3 text-center font-mono text-slate-500">
        {task.current_generation > 0 ? efficiency.toFixed(2) : "-"}
      </td>
      <td className="py-3 text-center font-mono text-slate-500">
        {task.current_generation}/{task.max_generations}
      </td>
      <td className="py-3 text-center">
        <Tag
          color={isCompleted ? "success" : undefined}
          className="!m-0 !text-[10px]"
        >
          {isCompleted ? "完成" : isStopped ? "停止" : task.status}
        </Tag>
      </td>
      <td className="py-3 text-right">
        <div className="flex items-center justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          <AntButton
            type="text"
            size="small"
            onClick={onView}
            aria-label="查看"
          >
            <Eye className="h-3.5 w-3.5" />
          </AntButton>
          {task.champion_dna && (
            <AntButton
              type="text"
              size="small"
              onClick={onSeedEvolve}
              aria-label="继续进化"
              style={{ color: "#a78bfa" }}
            >
              <Dna className="h-3.5 w-3.5" />
            </AntButton>
          )}
        </div>
      </td>
    </tr>
  );
});
