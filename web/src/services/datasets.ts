import { api } from "./api";
import type {
  AvailableSourcesResponse,
  ChartIndicatorsResponse,
  Dataset,
  DatasetListResponse,
  OhlcvData,
} from "@/types/api";

export async function getDatasets(
  params?: { symbol?: string; interval?: string }
): Promise<DatasetListResponse> {
    return api.get("/api/data/datasets", { params });
}

export async function getDataset(id: string): Promise<Dataset> {
    return api.get(`/api/data/datasets/${id}`);
}

export async function importCsv(formData: FormData): Promise<{
  dataset_id: string;
  symbol: string;
  interval: string;
  rows_imported: number;
}> {
    return api.post("/api/data/import", { body: formData, timeout: 120000 });
}

export async function importCsvBatch(formData: FormData): Promise<{
  dataset_id: string;
  symbol: string;
  interval: string;
  rows_imported: number;
  files_processed: number;
}> {
    return api.post("/api/data/import-batch", { body: formData, timeout: 300000 });
}

export async function deleteDataset(id: string): Promise<void> {
  await api.delete(`/api/data/datasets/${id}`);
}

export async function getOhlcv(
  id: string,
  params?: { start?: string; end?: string; limit?: number }
): Promise<OhlcvData> {
    return api.get(`/api/data/datasets/${id}/ohlcv`, { params });
}

export async function getDatasetPreview(
  id: string,
  params?: { rows?: number }
): Promise<{ rows: Record<string, unknown>[]; columns: string[] }> {
    return api.get(`/api/data/datasets/${id}/preview`, { params });
}

export async function getAvailableSources(): Promise<AvailableSourcesResponse> {
    return api.get("/api/data/available-sources");
}

export async function getOhlcvBySymbol(
  symbol: string,
  timeframe: string,
  params?: { start?: string; end?: string; limit?: number }
): Promise<OhlcvData> {
    return api.get(`/api/data/ohlcv/${symbol}/${timeframe}`, { params });
}

export async function getChartIndicators(
  symbol: string,
  timeframe: string,
  params?: {
    start?: string;
    end?: string;
    ema_periods?: string;
    boll_enabled?: boolean;
    boll_period?: number;
    boll_std?: number;
    rsi_enabled?: boolean;
    rsi_period?: number;
    macd_enabled?: boolean;
    macd_fast?: number;
    macd_slow?: number;
    macd_signal?: number;
    kdj_enabled?: boolean;
    kdj_k_period?: number;
    kdj_d_period?: number;
    limit?: number;
  }
): Promise<ChartIndicatorsResponse> {
    return api.get(`/api/data/chart-indicators/${symbol}/${timeframe}`, { params });
}
