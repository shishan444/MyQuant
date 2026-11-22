import { api } from "./api";
import type {
  Strategy,
  StrategyListResponse,
} from "@/types/api";

export async function getStrategies(params?: {
  symbol?: string;
  source?: string;
  tags?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}): Promise<StrategyListResponse> {
    return api.get("/api/strategies", { params });
}

/**
 * 按状态显式查询（alignment-batch D4）：列表端点 status 缺省视图排除
 * draft，六态全可见需补一次显式 status=draft 查询（单值过滤参数）。
 */
export async function getStrategiesByStatus(status: string, params?: {
  sort_by?: string;
  sort_order?: "asc" | "desc";
  limit?: number;
}): Promise<StrategyListResponse> {
  return api.get("/api/strategies", { params: { status, ...params } });
}

export async function getStrategy(id: string): Promise<Strategy> {
    return api.get(`/api/strategies/${id}`);
}

export async function createStrategy(payload: {
  name?: string;
  dna: unknown;
  symbol: string;
  timeframe: string;
  source: string;
  source_task_id?: string;
  tags?: string;
  notes?: string;
}): Promise<Strategy> {
    return api.post("/api/strategies", { json: payload });
}

export async function updateStrategy(
  id: string,
  payload: Partial<{ name: string; tags: string; notes: string }>
): Promise<Strategy> {
    return api.put(`/api/strategies/${id}`, { json: payload });
}

export async function deleteStrategy(id: string): Promise<void> {
    await api.delete(`/api/strategies/${id}`);
}

// verify 族（history/sessions/results/stream 四端点）随 verify 会话体系
// 退役删除——fix-replay-gap-alignment U8（批 2 曾保活至消费方收口）。
