import { api } from "./api";
import type {
  EvolutionTask,
  EvolutionTaskListResponse,
  EvolutionHistoryResponse,
  EvolvedStrategy,
  DiscoveredStrategy,
  RequirementsConfig,
  EvolutionHistoryRecord,
} from "@/types/api";

export async function getEvolutionTasks(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<EvolutionTaskListResponse> {
    return api.get("/api/evolution/tasks", { params });
}

export async function getEvolutionTask(id: string): Promise<EvolutionTask> {
    return api.get(`/api/evolution/tasks/${id}`);
}

export async function createEvolutionTask(payload: {
  initial_dna?: unknown;
  symbol: string;
  timeframe: string;
  target_score?: number;
  score_template?: string;
  population_size?: number;
  max_generations?: number;
  indicator_pool?: string[];
  timeframe_pool?: string[];
  mode?: "auto" | "seed";
  leverage?: number;
  direction?: "long" | "short" | "mixed";
  data_start?: string;
  data_end?: string;
  strategy_threshold?: number;
  min_annual_return?: number;
  max_drawdown_limit?: number;
  requirements?: RequirementsConfig;
}): Promise<EvolutionTask> {
    return api.post("/api/evolution/tasks", { json: payload });
}

export async function pauseEvolutionTask(id: string): Promise<EvolutionTask> {
    return api.post(`/api/evolution/tasks/${id}/pause`);
}

export async function stopEvolutionTask(id: string): Promise<EvolutionTask> {
    return api.post(`/api/evolution/tasks/${id}/stop`);
}

export async function resumeEvolutionTask(id: string): Promise<EvolutionTask> {
    return api.post(`/api/evolution/tasks/${id}/resume`);
}

export async function getEvolutionHistory(
  id: string,
  params?: { limit?: number; offset?: number }
): Promise<EvolutionHistoryResponse> {
  const data = await api.get<{
    generations?: EvolutionHistoryRecord[];
    records?: EvolutionHistoryRecord[];
  }>(`/api/evolution/tasks/${id}/history`, { params });
  // Backend returns { task_id, generations }, frontend expects { records, total }
  const generations = data.generations ?? data.records ?? [];
  return {
    records: generations,
    total: generations.length,
  };
}

export async function getTaskStrategies(taskId: string): Promise<{
  task_id: string;
  strategies: EvolvedStrategy[];
}> {
  return api.get(`/api/evolution/tasks/${taskId}/strategies`);
}

export async function getDiscoveredStrategies(
  taskId: string,
  params?: { min_score?: number; limit?: number }
): Promise<DiscoveredStrategy[]> {
    return api.get(`/api/evolution/tasks/${taskId}/discovered-strategies`, { params });
}

export async function getAllDiscoveredStrategies(
  params?: { min_score?: number; limit?: number }
): Promise<DiscoveredStrategy[]> {
    return api.get("/api/evolution/strategies", { params });
}
