/**
 * 05 进化中心·产出策略两源呈现（alignment-batch D3）。
 * 数据源 GET /api/evolution/tasks/{id}/strategies：冠军（任务表 champion_dna
 * 持久值）与代际快照（snapshot 表 top-N）两来源，逐条标注来源——
 * 两源数据形状同构（EvolvedStrategy），来源语义不同须可区分。
 */
import { Tag } from "antd";
import type { EvolvedStrategy } from "@/types/api";

interface EvolvedStrategySourcesProps {
  strategies: EvolvedStrategy[];
  loading: boolean;
  error: string | null;
}

function sourceBadge(s: EvolvedStrategy) {
  if (s.source === "champion") {
    return <Tag color="warning" className="!m-0 !text-[10px]">冠军 champion</Tag>;
  }
  return (
    <Tag className="!m-0 !text-[10px]">
      快照 snapshot{s.generation != null ? ` · Gen ${s.generation}` : ""}
    </Tag>
  );
}

/** sl_mode 档位展示标签（Strategies.tsx SL_MODE_LABELS 同形制；取值单源=后端 dna.py SL_MODES 四档） */
const SL_MODE_LABELS: Record<string, string> = { pct: "百分比档", atr: "ATR 档", trail: "移动档", series: "序列档" };

function DnaSummary({ dna }: { dna: EvolvedStrategy["dna"] }) {
  const risk = dna.risk_genes;
  // 方向显式四分支：short/mixed/long 之外（含 risk_genes 整体缺失）不兜底显示
  const direction =
    risk?.direction === "short" ? "做空"
    : risk?.direction === "mixed" ? "混合"
    : risk?.direction === "long" ? "做多"
    : null;
  const geneCount = (dna.signal_genes?.length ?? 0) + (dna.layers?.length ?? 0);
  const stopLoss = risk?.stop_loss ?? null;
  const takeProfit = risk?.take_profit ?? 0;
  const slMode = risk?.sl_mode ?? null;
  const parts = [
    direction,
    slMode ? SL_MODE_LABELS[slMode] ?? slMode : null,
    // series 档止损线由 sl_series_source 价格列承载，stop_loss 物化残留是
    // dataclass 缺省（dna.py 默认 0.05）而非声明值，标注防止误读
    stopLoss != null
      ? `止损 ${(stopLoss * 100).toFixed(1)}%${slMode === "series" ? "（非声明值）" : ""}`
      : null,
    takeProfit > 0 ? `止盈 ${(takeProfit * 100).toFixed(1)}%` : null,
    `${geneCount} 个基因`,
  ].filter((p): p is string => p != null);
  return (
    <span className="text-[11px] text-slate-500">
      {parts.join(" · ")}
    </span>
  );
}

export function EvolvedStrategySources({ strategies, loading, error }: EvolvedStrategySourcesProps) {
  if (loading) {
    return (
      <p className="text-xs text-slate-500" data-testid="evolved-sources-loading">
        加载产出策略...
      </p>
    );
  }
  if (error) {
    return (
      <p className="text-xs text-red-400" data-testid="evolved-sources-error">
        产出策略加载失败：{error}
      </p>
    );
  }
  if (strategies.length === 0) {
    return (
      <p className="text-xs text-slate-500" data-testid="evolved-sources-empty">
        暂无产出策略（任务尚未产生冠军或代际快照）
      </p>
    );
  }

  const championCount = strategies.filter((s) => s.source === "champion").length;
  const snapshotCount = strategies.length - championCount;

  return (
    <div className="flex flex-col gap-2">
      <p className="text-[11px] text-slate-500" data-testid="evolved-sources-count">
        冠军 {championCount} · 快照 {snapshotCount}（冠军=任务持久的最优个体，快照=代际 Top 记录）
      </p>
      {strategies.map((s, idx) => (
        <div
          key={s.strategy_id || idx}
          data-testid="evolved-source-item"
          data-source={s.source}
          className="rounded-lg border border-slate-700/30 px-3 py-2"
        >
          <div className="flex items-center justify-between gap-2">
            {sourceBadge(s)}
            <span className="font-mono text-xs text-slate-300" title="评分">
              {s.score.toFixed(1)}
            </span>
          </div>
          <div className="mt-1">
            <DnaSummary dna={s.dna} />
          </div>
        </div>
      ))}
    </div>
  );
}
