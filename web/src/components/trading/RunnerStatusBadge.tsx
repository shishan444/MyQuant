import { cn } from "@/lib/utils";

interface RunnerStatusBadgeProps {
  /** 调度器状态（GET /api/paper-trading/runner-status） */
  status:
    | {
        max_running_tasks: number | null;
        running_count: number;
        active_task_ids: string[];
        pending_depth: number;
      }
    | undefined;
}

/** 调度器状态徽标：运行数/上限+pending 排队深度（队列可见性面） */
export function RunnerStatusBadge({ status }: RunnerStatusBadgeProps) {
  const max = status?.max_running_tasks ?? null;
  const running = status?.running_count ?? 0;
  const pending = status?.pending_depth ?? 0;
  // 无调度器（max null）或满载视为不可受理新运行
  const hasCapacity = max != null ? running < max : false;
  const label =
    max != null
      ? `调度 ${running}/${max}${pending > 0 ? ` · 排队 ${pending}` : ""}`
      : "调度器未就绪";

  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-text-muted"
      title={`活跃任务 ${running}，上限 ${max ?? "-"}，排队深度 ${pending}`}
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          running > 0 ? "bg-profit animate-pulse" : hasCapacity ? "bg-profit" : "bg-loss",
        )}
      />
      {label}
    </span>
  );
}
