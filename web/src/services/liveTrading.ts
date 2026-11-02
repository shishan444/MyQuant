import { api } from "@/services/api";

/**
 * 08 实盘交易（demo 域）服务层（live-trading-preview）。
 * 消费 live_trading.py 壳 CRUD——demo 域任务骨架真实数据；
 * B4 外部干预流/B6 决策快照流后端不存在（页面呈现预览样例）。
 */

/** live_trading.py LiveTradingTaskResponse 契约（壳端点，手写类型——未入 openapi 外部类型） */
export interface LiveTradingTask {
  task_id: string;
  symbol: string;
  timeframe: string;
  status: string; // pending/running/stopped/error（壳值域）
  config_json?: string | null;
  direction: string;
  position_side: string;
  position_qty: number;
  position_entry: number;
  balance: number;
  equity: number;
  unrealized_pnl: number;
  total_trades: number;
  total_pnl: number;
  last_bar_time?: string | null;
  heartbeat_at?: string | null;
  created_at: string;
}

export async function listLiveTasks(status?: string): Promise<LiveTradingTask[]> {
  const out = await api.get<{ tasks: LiveTradingTask[]; total: number }>(
    "/api/live-trading/tasks",
    { params: status ? { status } : undefined },
  );
  return out.tasks ?? [];
}
