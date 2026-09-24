/** Scene verification types. */

export interface SceneTypeInfo {
  id: string;
  label: string;
  description: string;
  group?: string;
  parent?: string;
}

export interface SceneVerifyRequest {
  symbol: string;
  timeframe: string;
  scene_type: string;
  params: Record<string, unknown>;
  horizons?: number[];
  data_start?: string;
  data_end?: string;
}

export interface HorizonSummary {
  horizon: number;
  total_triggers: number;
  win_rate: number;
  win_rate_ci?: [number, number]; // Wilson 95% CI（百分比域）
  avg_return_pct: number;
  median_return_pct: number;
  avg_max_gain_pct: number;
  avg_max_loss_pct: number;
  avg_bars_to_peak: number;
  distribution: Array<{ range: [number, number]; count: number }>;
  percentiles: Record<string, number>;
}

export interface SceneTriggerDetail {
  id: number;
  timestamp: string;
  trigger_price: number;
  indicator_snapshot: Record<string, number>;
  pattern_subtype?: string;
  forward_stats: Record<string, {
    close_pct: number;
    max_gain_pct: number;
    max_loss_pct: number;
    bars_to_peak: number;
    bars_to_trough: number;
    is_partial: boolean;
  }>;
}

export interface SceneVerifyResponse {
  scene_type: string;
  scene_label: string;
  scene_description: string;
  total_triggers: number;
  statistics_by_horizon: HorizonSummary[];
  trigger_details: SceneTriggerDetail[];
  warnings: string[];
}
