import { api } from "@/services/api";

/**
 * 09 数据管理服务层（data-management-revamp 前端组）。
 * 月矩阵 v2 两端点消费（integrity/monthly-matrix+freshness/status）；
 * 数据集概要沿既有 useDatasets。
 */

export type CellStatus = "green" | "yellow" | "gray";

export interface MonthCell {
  timeframe: string;
  status: CellStatus;
  bars_actual: number;
  bars_expected: number;
  gap_count: number;
  first_gap: { start: string; end: string } | null;
}

export interface MonthRow {
  month: string;
  cells: MonthCell[];
}

export interface LongPeriodRow {
  timeframe: string;
  status: CellStatus;
  coverage: { first_bar: string | null; last_bar: string | null; bars: number };
  gap_count: number;
  gaps: Array<{ start: string; end: string }>;
}

export interface MonthlyMatrixResponse {
  symbol: string;
  generated_at: string;
  months_back: number;
  short_period: { timeframes: string[]; matrix: MonthRow[] };
  long_period: LongPeriodRow[];
}

export interface FreshnessStatusResponse {
  kline: { last_run: string | null; keys: Record<string, string> } | null;
  derivs: {
    last_run: string | null;
    symbols: Record<string, { funding?: unknown; oi?: unknown }>;
  } | null;
}

export async function getMonthlyMatrix(params: {
  symbol: string;
  months_back?: number;
}): Promise<MonthlyMatrixResponse> {
  return api.get("/api/data/integrity/monthly-matrix", { params });
}

export async function getFreshnessStatus(): Promise<FreshnessStatusResponse> {
  return api.get("/api/data/freshness/status");
}
