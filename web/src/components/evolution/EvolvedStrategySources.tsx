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

function DnaSummary({ dna }: { dna: EvolvedStrategy["dna"] }) {
  const risk = dna.risk_genes;
  const direction = risk?.direction === "short" ? "做空" : risk?.direction === "mixed" ? "混合" : "做多";
  const geneCount = (dna.signal_genes?.length ?? 0) + (dna.layers?.length ?? 0);
  return (
    <span className="text-[11px] text-slate-500">
      {direction} · 止损 {((risk?.stop_loss ?? 0.03) * 100).toFixed(1)}% ·{" "}
      {(risk?.take_profit ?? 0) * 100 > 0 ? `止盈 ${((risk.take_profit ?? 0) * 100).toFixed(1)}% · ` : ""}
      {geneCount} 个基因
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
