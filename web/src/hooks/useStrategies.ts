import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/services/strategies";
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

// （useCreateStrategy 保存 mutation 随 Lab 验证保存动作链退役零消费删除——
//  fix-replay-gap-alignment U5 批次收口；api.createStrategy service 层保留
//  〔活端点 POST /api/strategies，services 直测覆盖〕）

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

// useVerifyStream/useVerifySessions/useSessionResults 随 verify 会话体系
// 退役删除——fix-replay-gap-alignment U8（批 2 曾保活至消费方收口）。
