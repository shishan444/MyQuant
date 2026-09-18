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
import {
  listTradingTasks,
  getTradingTask,
  createTradingTask,
  stopTradingTask,
  restartTradingTask,
  getTradingTrades,
  getTradingRunnerStatus,
  getTradingEquity,
  getTradingMetrics,
  deleteTradingTask,
  type CreateTradingTaskParams,
} from "@/services/trading";

// -- Query key factory --

const tradingKeys = {
  all: ["trading"] as const,
  tasks: () => [...tradingKeys.all, "tasks"] as const,
  task: (id: string) => [...tradingKeys.all, "task", id] as const,
  trades: (id: string) => [...tradingKeys.all, "trades", id] as const,
  equity: (id: string) => [...tradingKeys.all, "equity", id] as const,
  metrics: (id: string) => [...tradingKeys.all, "metrics", id] as const,
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

export function tradingTradesOptions(taskId: string, limit = 50) {
  return queryOptions({
    queryKey: tradingKeys.trades(taskId),
    queryFn: () => getTradingTrades(taskId, limit),
    enabled: !!taskId,
  });
}

export function tradingEquityOptions(taskId: string) {
  return queryOptions({
    queryKey: tradingKeys.equity(taskId),
    queryFn: () => getTradingEquity(taskId),
    enabled: !!taskId,
  });
}

export function tradingMetricsOptions(taskId: string) {
  return queryOptions({
    queryKey: tradingKeys.metrics(taskId),
    queryFn: () => getTradingMetrics(taskId),
    enabled: !!taskId,
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

export function useTradingTrades(taskId: string, limit?: number) {
  return useQuery(tradingTradesOptions(taskId, limit));
}

export function useTradingEquity(taskId: string) {
  return useQuery(tradingEquityOptions(taskId));
}

export function useTradingMetrics(taskId: string) {
  return useQuery(tradingMetricsOptions(taskId));
}

export function useRunnerStatus() {
  return useQuery(runnerStatusOptions());
}

export function useCreateTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: CreateTradingTaskParams) => createTradingTask(params),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success("Task created");
    },
    onError: (err: Error) => {
      toast.error(`Failed to create task: ${err.message}`);
    },
  });
}

export function useStopTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => stopTradingTask(taskId),
    onSuccess: (_data, taskId) => {
      qc.invalidateQueries({ queryKey: tradingKeys.task(taskId) });
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success("Task stopped");
    },
    onError: (err: Error) => {
      toast.error(`Failed to stop: ${err.message}`);
    },
  });
}

export function useRestartTradingTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => restartTradingTask(taskId),
    onSuccess: (newTask) => {
      qc.invalidateQueries({ queryKey: tradingKeys.tasks() });
      toast.success(`New task created: ${newTask.strategy_name || newTask.symbol}`);
    },
    onError: (err: Error) => {
      toast.error(`Failed to restart: ${err.message}`);
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
      toast.error(`Failed to delete: ${err.message}`);
    },
  });
}

// -- WebSocket --

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
        qc.invalidateQueries({ queryKey: tradingKeys.trades(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.equity(taskId) });
        qc.invalidateQueries({ queryKey: tradingKeys.metrics(taskId) });
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
      if (result.event.type === "position_update" || result.event.type === "task_started") {
        scheduleInvalidation();
      }
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
