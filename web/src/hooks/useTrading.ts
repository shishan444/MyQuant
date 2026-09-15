import {
  useQuery,
  useMutation,
  useQueryClient,
  queryOptions,
} from "@tanstack/react-query";
import { useEffect, useRef, useCallback, useState } from "react";
import { toast } from "sonner";
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
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [isConnected, setIsConnected] = useState(false);

  const scheduleInvalidation = useCallback(() => {
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(() => {
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

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = import.meta.env.VITE_WS_URL
      ? new URL(import.meta.env.VITE_WS_URL).host
      : window.location.host;
    const wsUrl = `${protocol}//${host}/ws/trading/${taskId}`;

    const connect = () => {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "position_update" || msg.type === "task_started") {
            scheduleInvalidation();
          }
        } catch {
          // ignore
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        setIsConnected(false);
        reconnectTimer.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      wsRef.current?.close();
      wsRef.current = null;
      clearTimeout(reconnectTimer.current);
      setIsConnected(false);
    };
  }, [taskId, scheduleInvalidation]);

  return isConnected;
}
