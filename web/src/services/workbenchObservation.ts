/**
 * 观察台 B 列/C 列观察数据服务（fe-workbench-wiring）：
 * market-snapshot 五维/hypotheses active/log 流——全只读。
 * D8/D9/D11 端点未落地（be-workbench-support 未归档），对应区按底注形制交付。
 * market-snapshot 缺省四档含 1d——BTCUSDT 1d 现存数据不满足 1d 轴 warmup
 * （2026-11-03 实测 422 RESEARCH_WARMUP_INSUFFICIENT），观察面固定取 4h 主档
 * （与原型「·1h·」示例同为单档主读数语义；as_of/warmup 不足时按 W0 错误态呈现）。
 */
import { api } from "./api";

/** 五维读数（一档内的五域——形制实测 2026-11-03） */
export interface SnapshotTrend {
  adx?: number;
  state?: string;
}
export interface SnapshotMomentum {
  rsi?: number;
  pct?: number;
}
export interface SnapshotVolatility {
  atr_pct?: number;
  atr_pct_pct?: number;
  bb_bw?: number;
  bb_bw_pct?: number;
}
export interface SnapshotVolume {
  rvol?: number;
  pct?: number;
}
export interface SnapshotDerivatives {
  funding?: { status?: string; funding_z?: number; pct?: number };
  oi?: { status?: string; oi_change_rate?: number; pct?: number };
}
export interface SnapshotTimeframe {
  trend?: SnapshotTrend;
  momentum?: SnapshotMomentum;
  volatility?: SnapshotVolatility;
  volume?: SnapshotVolume;
  derivatives?: SnapshotDerivatives;
  window_bars?: number;
  as_of?: string;
}
export interface MarketSnapshotResponse {
  symbol: string;
  as_of: string;
  window_days?: number;
  timeframes: Record<string, SnapshotTimeframe>;
}
export async function getMarketSnapshot(
  symbol = "BTCUSDT",
  timeframes = "4h",
): Promise<MarketSnapshotResponse> {
  return api.get("/api/research/market-snapshot", {
    params: { symbol, timeframes },
  });
}

/** 在途假设卡组（GET /api/research/hypotheses?status=active&page_size=50——items 形制实测） */
export interface ActiveHypothesisRow {
  hypothesis_id: string;
  statement?: string;
  status: string;
  direction?: string;
  conditions?: {
    when_conditions?: Array<{
      subject?: { indicator?: string };
      action?: string;
      target?: number;
    }>;
  };
  data_ranges?: Array<{ symbol: string; timeframe: string }>;
  created_at: string;
}
export interface HypothesesResponse {
  items: ActiveHypothesisRow[];
  total: number;
}
export async function getActiveHypotheses(): Promise<HypothesesResponse> {
  return api.get("/api/research/hypotheses", {
    params: { status: "active", page_size: 50 },
  });
}

/** 研究日志流（GET /api/research/log?page_size=30——items desc 最新在前，实测形制） */
export interface ResearchLogRow {
  log_id: string;
  hypothesis_id: string | null;
  entry_type: string;
  ref_id?: string | null;
  summary?: string | null;
  created_at: string;
}
export interface ResearchLogResponse {
  items: ResearchLogRow[];
  total: number;
}
export async function getResearchLog(pageSize = 30): Promise<ResearchLogResponse> {
  return api.get("/api/research/log", { params: { page_size: pageSize } });
}
