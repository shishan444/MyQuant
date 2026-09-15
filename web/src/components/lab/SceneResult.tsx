/** Scene verification result display. */
import { useMemo } from "react";
import { TrendingUp, TrendingDown, Target, BarChart3 } from "lucide-react";
import type { SceneVerifyResponse, SceneTriggerDetail } from "@/types/scene";

interface SceneResultProps {
  result: SceneVerifyResponse;
  onLocateTrigger?: (trigger: SceneTriggerDetail) => void;
}

export function SceneResult({ result, onLocateTrigger }: SceneResultProps) {
  const bestHorizon = useMemo(() => {
    if (!result.statistics_by_horizon.length) return null;
    return result.statistics_by_horizon.reduce((best, h) =>
      h.avg_return_pct > best.avg_return_pct ? h : best,
    );
  }, [result.statistics_by_horizon]);

  const firstHorizon = result.statistics_by_horizon[0];

  return (
    <div className="flex flex-col gap-4">
      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-2">
        <StatCard
          icon={<Target className="h-4 w-4 text-blue-400" />}
          label="触发次数"
          value={String(result.total_triggers)}
        />
        <StatCard
          icon={<TrendingUp className="h-4 w-4 text-emerald-400" />}
          label="胜率"
          value={firstHorizon ? `${firstHorizon.win_rate}%` : "-"}
          sublabel={firstHorizon ? `${firstHorizon.horizon}根K线` : undefined}
        />
        <StatCard
          icon={<BarChart3 className="h-4 w-4 text-amber-400" />}
          label="平均收益"
          value={firstHorizon ? `${firstHorizon.avg_return_pct > 0 ? "+" : ""}${firstHorizon.avg_return_pct}%` : "-"}
          positive={firstHorizon ? firstHorizon.avg_return_pct > 0 : undefined}
        />
        <StatCard
          icon={<TrendingDown className="h-4 w-4 text-rose-400" />}
          label="平均最大亏损"
          value={firstHorizon ? `${firstHorizon.avg_max_loss_pct}%` : "-"}
        />
      </div>

      {/* Best horizon highlight */}
      {bestHorizon && bestHorizon !== firstHorizon && (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-400/5 px-3 py-2">
          <span className="text-[11px] text-emerald-400">
            最优观察窗口: {bestHorizon.horizon}根K线
          </span>
          <span className="text-[11px] text-slate-400">
            胜率 {bestHorizon.win_rate}% | 平均收益 {bestHorizon.avg_return_pct}%
          </span>
        </div>
      )}

      {/* Per-horizon breakdown table */}
      {result.statistics_by_horizon.length > 0 && (
        <div className="rounded-lg border border-slate-700/20">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-slate-700/20 text-slate-500">
                <th className="px-3 py-1.5 text-left">窗口</th>
                <th className="px-2 py-1.5 text-right">胜率</th>
                <th className="px-2 py-1.5 text-right">平均收益</th>
                <th className="px-2 py-1.5 text-right">中位收益</th>
                <th className="px-2 py-1.5 text-right">平均最大增益</th>
                <th className="px-2 py-1.5 text-right">平均最大亏损</th>
                <th className="px-2 py-1.5 text-right">平均峰值时间</th>
              </tr>
            </thead>
            <tbody>
              {result.statistics_by_horizon.map((h) => (
                <tr
                  key={h.horizon}
                  className="border-b border-slate-700/10 text-slate-300"
                >
                  <td className="px-3 py-1.5 font-mono text-amber-400">
                    {h.horizon}根K线
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {h.win_rate}%
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono ${
                      h.avg_return_pct > 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {h.avg_return_pct > 0 ? "+" : ""}
                    {h.avg_return_pct}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {h.median_return_pct}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-emerald-400">
                    +{h.avg_max_gain_pct}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-rose-400">
                    {h.avg_max_loss_pct}%
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-500">
                    {h.avg_bars_to_peak}根K线
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Distribution for primary horizon */}
      {firstHorizon && firstHorizon.distribution.length > 0 && (
        <DistributionBarChart buckets={firstHorizon.distribution} />
      )}

      {/* Trigger list (top 20) */}
      {result.trigger_details.length > 0 && (
        <TriggerList triggers={result.trigger_details} onLocate={onLocateTrigger} />
      )}

      {/* Warnings */}
      {result.warnings.length > 0 && (
        <div className="flex flex-col gap-1">
          {result.warnings.map((w, i) => (
            <p key={i} className="text-[11px] text-amber-500">{w}</p>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  icon,
  label,
  value,
  sublabel,
  positive,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sublabel?: string;
  positive?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-slate-700/20 bg-white/[0.01] p-2.5">
      <div className="flex items-center gap-1.5">
        {icon}
        <span className="text-[10px] text-slate-500">{label}</span>
      </div>
      <span
        className={`text-sm font-mono font-medium ${
          positive === true
            ? "text-emerald-400"
            : positive === false
              ? "text-rose-400"
              : "text-slate-200"
        }`}
      >
        {value}
      </span>
      {sublabel && (
        <span className="text-[10px] text-slate-600">{sublabel}</span>
      )}
    </div>
  );
}

function DistributionBarChart({
  buckets,
}: {
  buckets: Array<{ range: [number, number]; count: number }>;
}) {
  const maxCount = Math.max(...buckets.map((b) => b.count), 1);

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] text-slate-500">收益分布 (首窗口)</span>
      <div className="flex items-end gap-0.5 h-16">
        {buckets.map((b, i) => {
          const heightPct = (b.count / maxCount) * 100;
          const midVal = (b.range[0] + b.range[1]) / 2;
          const isPositive = midVal >= 0;
          return (
            <div
              key={i}
              className="flex-1 flex flex-col items-center gap-0.5"
              title={`${b.range[0]}% ~ ${b.range[1]}%: ${b.count}次`}
            >
              <div
                className={`w-full rounded-t transition-all ${
                  isPositive ? "bg-emerald-400/40" : "bg-rose-400/40"
                }`}
                style={{ height: `${heightPct}%` }}
              />
              <span className="text-[8px] text-slate-600 font-mono">
                {b.range[0] > 0 ? "+" : ""}{b.range[0]}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const SUBTYPE_LABELS: Record<string, string> = {
  double_top: "M",
  head_shoulders_top: "H&S",
  triple_top: "T",
};

const SUBTYPE_BADGE_COLORS: Record<string, string> = {
  double_top: "text-amber-400",
  head_shoulders_top: "text-purple-400",
  triple_top: "text-blue-400",
};

function TriggerList({
  triggers,
  onLocate,
}: {
  triggers: SceneTriggerDetail[];
  onLocate?: (t: SceneTriggerDetail) => void;
}) {
  const displayTriggers = triggers.slice(0, 20);

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] text-slate-500">
        触发记录 ({triggers.length > 20 ? `前20 / 共${triggers.length}` : triggers.length})
      </span>
      <div className="max-h-48 overflow-y-auto rounded-lg border border-slate-700/20">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-slate-900/95">
            <tr className="text-slate-500">
              <th className="px-2 py-1 text-left">#</th>
              <th className="px-2 py-1 text-left">类型</th>
              <th className="px-2 py-1 text-left">时间</th>
              <th className="px-2 py-1 text-right">价格</th>
              <th className="px-2 py-1 text-right">6K收益</th>
              <th className="px-2 py-1 text-right">24K收益</th>
              <th className="px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            {displayTriggers.map((t) => {
              const h6 = t.forward_stats["6"];
              const h24 = t.forward_stats["24"];
              const sub = t.pattern_subtype;
              const badge = sub ? (SUBTYPE_LABELS[sub] ?? sub) : "";
              const badgeColor = sub ? (SUBTYPE_BADGE_COLORS[sub] ?? "text-slate-400") : "text-slate-500";
              return (
                <tr key={t.id} className="border-t border-slate-700/10 text-slate-300">
                  <td className="px-2 py-1 text-slate-500">{t.id}</td>
                  <td className={`px-2 py-1 font-mono text-[10px] ${badgeColor}`}>
                    {badge}
                  </td>
                  <td className="px-2 py-1 font-mono">{t.timestamp.slice(0, 16)}</td>
                  <td className="px-2 py-1 text-right font-mono">{t.trigger_price.toFixed(2)}</td>
                  <td
                    className={`px-2 py-1 text-right font-mono ${
                      h6 && h6.close_pct > 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {h6 ? `${h6.close_pct > 0 ? "+" : ""}${h6.close_pct}%` : "-"}
                  </td>
                  <td
                    className={`px-2 py-1 text-right font-mono ${
                      h24 && h24.close_pct > 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {h24 ? `${h24.close_pct > 0 ? "+" : ""}${h24.close_pct}%` : "-"}
                  </td>
                  <td className="px-2 py-1">
                    {onLocate && (
                      <button
                        type="button"
                        className="text-[10px] text-slate-500 hover:text-amber-400"
                        onClick={() => onLocate(t)}
                      >
                        定位
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
