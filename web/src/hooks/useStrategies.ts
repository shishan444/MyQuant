import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/services/strategies";
import { createStreamHook } from "./createStreamHook";
import { toast } from "sonner";

export const strategiesKeys = {
  all: ["strategies"] as const,
  list: (filters?: Record<string, string>) =>
    ["strategies", "list", filters] as const,
  detail: (id: string) => ["strategies", "detail", id] as const,
};

export function useStrategies(filters?: {
  symbol?: string;
  source?: string;
  tags?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  limit?: number;
}) {
  return queryOptions({
    queryKey: strategiesKeys.list(filters as Record<string, string>),
    queryFn: () => api.getStrategies(filters),
  });
}

export function useStrategy(id: string) {
  return queryOptions({
    queryKey: strategiesKeys.detail(id),
    queryFn: () => api.getStrategy(id),
    enabled: !!id,
  });
}

export function useRunBacktest() {
  return useMutation({
    mutationFn: api.runBacktest,
    onSuccess: () => toast.success("回测完成"),
    onError: (err) => toast.error(`回测失败: ${err.message}`, { duration: Infinity }),
  });
}

export function useCreateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createStrategy,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: strategiesKeys.all });
      toast.success("策略已保存");
    },
    onError: (err) => toast.error(`保存失败: ${err.message}`),
  });
}

export function useDeleteStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteStrategy,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: strategiesKeys.all });
      toast.success("策略已删除");
    },
    onError: (err) => toast.error(`删除失败: ${err.message}`),
  });
}

export function useUpdateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof api.updateStrategy>[1] }) =>
      api.updateStrategy(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: strategiesKeys.all });
      toast.success("策略已更新");
    },
    onError: (err) => toast.error(`更新失败: ${err.message}`),
  });
}

export function useVerifyStrategies() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.verifyStrategies,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...strategiesKeys.all, "verify-sessions"] });
      toast.success("验证完成");
    },
    onError: (err) => toast.error(`验证失败: ${err.message}`, { duration: Infinity }),
  });
}

// ── 流式 hooks 共用一个工厂；真实差异全部参数化 ──

type VerifyStreamPayload = Parameters<typeof api.verifyStrategiesStream>[0];
type VerifyStreamComplete = {
  session_id: string;
  summary: unknown[];
  results: unknown[];
};

/** 渐进式验证 SSE 流——存 session_id 并 invalidate verify-sessions。 */
export const useVerifyStream = createStreamHook<VerifyStreamPayload, VerifyStreamComplete>({
  streamFn: (payload, callbacks, signal) =>
    api.verifyStrategiesStream(payload, callbacks, signal),
  errorLabel: "验证失败",
  sessionIdKey: "session_id",
  onCompleteExtra: (qc) =>
    qc.invalidateQueries({ queryKey: [...strategiesKeys.all, "verify-sessions"] }),
});

type BatchBacktestStreamPayload = Parameters<typeof api.batchBacktestStream>[0];
type BatchBacktestStreamComplete = { summary: unknown[]; results: unknown[] };

/** Progressive batch backtest SSE stream. */
export const useBatchBacktestStream = createStreamHook<
  BatchBacktestStreamPayload,
  BatchBacktestStreamComplete
>({
  streamFn: (payload, callbacks, signal) =>
    api.batchBacktestStream(payload, callbacks, signal),
  errorLabel: "批量回测失败",
});

export function useVerifyHistory(strategyId?: string) {
  return queryOptions({
    queryKey: [...strategiesKeys.all, "verify-history", strategyId],
    queryFn: () => api.getVerifyHistory({ strategy_id: strategyId, limit: 500 }),
  });
}

export function useVerifySessions(limit?: number) {
  return queryOptions({
    queryKey: [...strategiesKeys.all, "verify-sessions", limit],
    queryFn: () => api.getVerifySessions(limit),
  });
}

export function useSessionResults(sessionId?: string | null) {
  return queryOptions({
    queryKey: [...strategiesKeys.all, "session-results", sessionId],
    queryFn: () => api.getSessionResults(sessionId as string),
    enabled: !!sessionId,
  });
}
