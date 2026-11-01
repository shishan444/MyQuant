/**
 * 05 进化中心·任务运行纯函数与 hook（alignment-batch D3）。
 * 与组件分离（react-refresh：组件文件只导出组件——workbenchConstants
 * 先例）；已耗时口径=契约登记表五「无进度条」的时间呈现。
 */
import { useEffect, useState } from "react";
import { isActiveStatus } from "@/lib/constants";
import type { EvolutionTask } from "@/types/api";

/** 秒数 → 中文可读时长（天/时/分/秒，从大到小取首个非零档+次档） */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  if (days > 0) return `${days}天${hours}时`;
  if (hours > 0) return `${hours}时${minutes}分`;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

/** 已耗时：运行中=created_at→当前时刻（每秒刷新）；终态=created_at→updated_at（定值） */
export function useElapsedSeconds(task: EvolutionTask): number {
  const [now, setNow] = useState(() => Date.now());
  const isActive = isActiveStatus(task.status);

  useEffect(() => {
    if (!isActive) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isActive]);

  const endMs = isActive
    ? now
    : Date.parse(task.updated_at || task.created_at);
  const startMs = Date.parse(task.created_at);
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return 0;
  return Math.max(0, (endMs - startMs) / 1000);
}
