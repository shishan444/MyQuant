import {
  useQuery,
  useMutation,
  useQueryClient,
  queryOptions,
} from "@tanstack/react-query";
import { useEffect, useRef, useCallback, useState } from "react";
import { toast } from "sonner";
import { validateTradingMessage } from "@/realtime/schemas";
import { createRealtimeConnection, type RealtimeConnection } from "@/realtime/connection";
import type { PaperTaskStatus } from "@/types/api";
import {
  listTradingTasks,
  getTradingTask,
  createTradingTask,
  stopTradingTask,
  cloneTradingTask,
  getTradingFills,
  getTradingFunding,
  getTradingRunnerStatus,
  getTradingEquity,
  getTradingReport,
  getTradingFinalReport,
  deleteTradingTask,
  type CreateTradingTaskParams,
} from "@/services/trading";

/** 终态集（clone/delete/结题报告的门槛——running/pending 非终态） */
const TERMINAL_STATUSES: readonly PaperTaskStatus[] = ["completed", "stopped", "error"];

export function isTerminalStatus(status: PaperTaskStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

// -- Query key factory --

const tradingKeys = {
  all: ["trading"] as const,
  tasks: () => [...tradingKeys.all, "tasks"] as const,
  task: (id: string) => [...tradingKeys.all, "task", id] as const,
  fills: (id: string) => [...tradingKeys.all, "fills", id] as const,
  equity: (id: string) => [...tradingKeys.all, "equity", id] as const,
  funding: (id: string) => [...tradingKeys.all, "funding", id] as const,
  report: (id: string) => [...tradingKeys.all, "report", id] as const,
  finalReport: (id: string) => [...tradingKeys.all, "finalReport", id] as const,
  runnerStatus: () => [...tradingKeys.all, "runnerStatus"] as const,
};

// -- Query options --

export function tradingTasksOptions() {
  return queryOptions({
    queryKey: tradingKeys.tasks(),
    queryFn: () => listTradingTasks(),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.tasks.some((t) => t.status === "running" || t.status === "pending")) {
        return 5000;
      }
      return false;
    },
  });
}

export function tradingTaskOptions(taskId: string) {
  return queryOptions({
    queryKey: tradingKeys.task(taskId),
    queryFn: () => getTradingTask(taskId),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" || status === "pending" ? 3000 : false;
    },
  });
}

export function tradingFillsOptions(taskId: string, limit = 500) {
  return queryOptions({
    queryKey: tradingKeys.fills(taskId),
    queryFn: () => getTradingFills(taskId, { limit }),
    enabled: !!taskId,
  });
}

export function tradingEquityOptions(taskId: string, limit = 500) {
  return queryOptions({
    queryKey: tradingKeys.equity(taskId),
    queryFn: () => getTradingEquity(taskId, { limit }),
    enabled: !!taskId,
  });
}

export function tradingFundingOptions(taskId: string, limit = 500) {
  return queryOptions({
    queryKey: tradingKeys.funding(taskId),
    queryFn: () => getTradingFunding(taskId, { limit }),
    enabled: !!taskId,
  });
}

export function tradingReportOptions(taskId: string) {
  return queryOptions({
    queryKey: tradingKeys.report(taskId),
    queryFn: () => getTradingReport(taskId),
    enabled: !!taskId,
    // 滚动报告任意状态可取；运行中随推进刷新
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" || status === "pending" ? 10000 : false;
    },
  });
}

export function tradingFinalReportOptions(taskId: string, isTerminal: boolean) {
  return queryOptions({
    queryKey: tradingKeys.finalReport(taskId),
    queryFn: () => getTradingFinalReport(taskId),
    // 结题门槛：未结题（pending/running）不发请求（后端 409 task not concluded）
    enabled: !!taskId && isTerminal,
    retry: false,
  });
}

export function runnerStatusOptions() {
  return queryOptions({
    queryKey: tradingKeys.runnerStatus(),
    queryFn: () => getTradingRunnerStatus(),
    refetchInterval: 10_000,
  });
}

// -- Hooks --

export function useTradingTasks() {
  return useQuery(tradingTasksOptions());
}

export function useTradingTask(taskId: string) {
  return useQuery(tradingTaskOptions(taskId));
}

export function useTradingFills(taskId: string, limit?: number) {
  return useQuery(tradingFillsOptions(taskId, limit));
}

export function useTradingEquity(taskId: string, limit?: number) {
  return useQuery(tradingEquityOptions(taskId, limit));
}

export function useTradingFunding(taskId: string, limit?: number) {
  return useQuery(tradingFundingOptions(taskId, limit));
}

export function useTradingReport(taskId: string) {
  return useQuery(tradingReportOptions(taskId));
}

/** 结题固化版（缺省最新版）——仅终态任务发请求，未结题由消费端按状态呈现 */
export function useTradingFinalReport(taskId: string, isTerminal: boolean) {
  return useQuery(tradingFinalReportOptions(taskId, isTerminal));
}

export function useRunnerStatus() {
  return useQuery(runnerStatusOptions());
}

export function useCreateTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: CreateTradingTaskParams) => createTradingTask(params),
    onSuccess: (task) => {
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success(`任务已创建（${task.task_id}）`);
    },
    onError: (err: Error) => {
      toast.error(`创建任务失败：${err.message}`);
    },
  });
}

export function useStopTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => stopTradingTask(taskId),
    onSuccess: (task) => {
      qc.invalidateQueries({ queryKey: tradingKeys.task(task.task_id) });
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success("已请求停止（运行中任务将在 bar 边界退出）");
    },
    onError: (err: Error) => {
      toast.error(`停止失败：${err.message}`);
    },
  });
}

/** 终态克隆重跑（新任务编号；运行/pending→409 由后端拒绝面兜底） */
export function useCloneTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => cloneTradingTask(taskId),
    onSuccess: (newTask) => {
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success(`已克隆新任务（${newTask.task_id}）`);
    },
    onError: (err: Error) => {
      toast.error(`克隆失败：${err.message}`);
    },
  });
}

export function useDeleteTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => deleteTradingTask(taskId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success("Task deleted");
    },
    onError: (err: Error) => {
      toast.error(`删除失败：${err.message}`);
    },
  });
}

// -- WebSocket --

/**
 * 事件分发驱动刷新：七事件+connect 快照均触发缓存失效（防抖 2s）——
 * task_started/task_update/fill_event 为过程推进，task_completed/
 * task_stopped/task_error 为终态迁移，task_snapshot 为重连恢复。
 */
export function useTradingWebSocket(taskId: string | null) {
  const qc = useQueryClient();
  // 定时器职责分离（ws-message-consumption 契约）：重连归适配层、业务防抖失效在本 hook
  const invalidationTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const connRef = useRef<RealtimeConnection | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const scheduleInvalidation = useCallback(() => {
    clearTimeout(invalidationTimerRef.current);
    invalidationTimerRef.current = setTimeout(() => {
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      if (taskId) {
        qc.invalidateQueries({ queryKey: tradingKeys.task(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.fills(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.equity(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.funding(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.report(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.finalReport(taskId) });
      }
    }, 2000);
  }, [qc, taskId]);

  useEffect(() => {
    if (!taskId) {
      setIsConnected(false);
      return;
    }

    // 连接管理（含协议消息消化/心跳/重连）归适配层，本 hook 只做事件分发
    const conn = createRealtimeConnection({ channel: "trading", taskId });
    connRef.current = conn;

    conn.onStatus(setIsConnected);
    conn.onMessage((raw) => {
      // 契约校验（src/realtime/contract.md）：协议消息归连接层、非法消息丢弃留痕
      const result = validateTradingMessage(raw);
      if (result.kind !== "event") return;
      scheduleInvalidation();
    });

    return () => {
      connRef.current?.close();
      connRef.current = null;
      clearTimeout(invalidationTimerRef.current);
      setIsConnected(false);
    };
  }, [taskId, scheduleInvalidation]);

  return isConnected;
}
