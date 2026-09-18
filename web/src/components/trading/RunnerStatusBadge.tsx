import { cn } from "@/lib/utils";

interface RunnerStatusBadgeProps {
  isAlive: boolean;
  activeTaskId: string | null;
}

export function RunnerStatusBadge({ isAlive }: RunnerStatusBadgeProps) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-text-muted">
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          isAlive ? "bg-profit animate-pulse" : "bg-loss"
        )}
      />
      {isAlive ? "Runner Online" : "Runner Offline"}
    </span>
  );
}
