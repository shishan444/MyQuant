import { Badge } from "@/components/ui/badge";
import { cn, getFitnessColor } from "@/lib/utils";
import type { DNA, TimeframeLayerModel } from "@/types/api";

interface StrategyDetailProps {
  dna: DNA;
  className?: string;
  champion_metrics?: {
    annual_return: number;
    sharpe_ratio: number;
    max_drawdown: number;
    win_rate: number;
    calmar_ratio: number;
    total_trades: number;
  };
  champion_dimension_scores?: Record<string, number>;
  champion_satisfaction?: Record<string, unknown>;
  champion_fitness?: number;
  champion_qualified?: boolean;
}

export function StrategyDetail({
  dna,
  className,
  champion_metrics,
  champion_dimension_scores,
  champion_satisfaction,
  champion_fitness,
  champion_qualified,
}: StrategyDetailProps) {
  const hasLayers = dna.layers && dna.layers.length > 0;

  const renderCondition = (
    gene: {
      indicator: string;
      params?: Record<string, unknown>;
      condition: { type: string; value?: number };
    },
    compact = false
  ) => {
    const condLabel: Record<string, string> = {
      lt: "<",
      gt: ">",
      le: "<=",
      ge: ">=",
      cross_above: "cross_above",
      cross_below: "cross_below",
      price_above: "price_above",
      price_below: "price_below",
    };
    const period = gene.params?.period;
    const periodStr = period ? `(${period})` : "";
    const cond = condLabel[gene.condition.type] ?? gene.condition.type;
    const val = gene.condition.value != null ? ` ${gene.condition.value}` : "";

    if (compact) {
      return `${gene.indicator}${periodStr} ${cond}${val}`;
    }
    return `${gene.indicator}${periodStr} ${cond}${val}`;
  };

  const metrics = champion_metrics;
  const dimScores = champion_dimension_scores;

  // Parse satisfaction data for per-dimension display
  const satisfactionEntries = champion_satisfaction
    ? Object.entries(champion_satisfaction) as Array<[string, { actual: number; required: number; ratio: number; met: boolean }]>
    : null;

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {/* Fitness and qualified status */}
      {(champion_fitness != null || champion_qualified != null) && (
        <div className="flex items-center gap-3 text-xs">
          {champion_fitness != null && (
            <span className="text-slate-500">
              适应度:{" "}
              <span className={champion_qualified === true ? "font-mono text-emerald-400" : champion_qualified === false ? "font-mono text-amber-400" : `font-mono ${getFitnessColor(champion_fitness)}`}>
                {champion_fitness.toFixed(2)}
              </span>
            </span>
          )}
          {champion_qualified != null && (
            <Badge
              variant="outline"
              className={cn(
                "text-[10px]",
                champion_qualified
                  ? "border-emerald-400/30 text-emerald-400"
                  : "border-slate-700/30 text-slate-500"
              )}
            >
              {champion_qualified ? "达标" : "未达标"}
            </Badge>
          )}
        </div>
      )}
      {/* MTF layers display */}
      {hasLayers &&
        (dna.layers as TimeframeLayerModel[]).map((layer, idx) => {
          const layers = dna.layers as TimeframeLayerModel[];
          const isMultiLayer = layers.length > 1;
          const role =
            isMultiLayer
              ? idx === 0
                ? "趋势过滤"
                : idx === layers.length - 1
                  ? "入场信号"
                  : "确认信号"
              : null;

          return (
            <div key={idx}>
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-medium text-slate-300">
                  Layer {idx + 1}: {layer.timeframe.toUpperCase()}
                </span>
                {role && (
                  <Badge
                    variant="outline"
                    className="border-slate-700/50 text-[10px] text-slate-500"
                  >
                    {role}
                  </Badge>
                )}
              </div>
              <div className="flex flex-col gap-0.5 rounded-lg border border-slate-700/30 p-2">
                {layer.signal_genes.length > 0 ? (
                  layer.signal_genes.map((gene, gi) => (
                    <div
                      key={gi}
                      className="text-xs text-slate-400"
                    >
                      {renderCondition(gene)}
                    </div>
                  ))
                ) : (
                  <span className="text-xs text-slate-600">无信号条件</span>
                )}
              </div>
            </div>
          );
        })}

      {/* Flat genes display (non-MTF) */}
      {!hasLayers && (
        <>
          <div>
            <span className="text-xs font-medium text-slate-400">
              入场条件 ({dna.logic_genes.entry_logic})
            </span>
            <div className="mt-1 flex flex-col gap-0.5 rounded-lg border border-slate-700/30 p-2">
              {dna.signal_genes
                .filter(
                  (g) =>
                    g.role === "entry_trigger" ||
                    g.role === "entry_guard"
                )
                .map((gene, gi) => (
                  <div key={gi} className="text-xs text-slate-400">
                    {renderCondition(gene)}
                  </div>
                ))}
            </div>
          </div>
          {dna.signal_genes.some(
            (g) =>
              g.role === "exit_trigger" || g.role === "exit_guard"
          ) && (
            <div>
              <span className="text-xs font-medium text-slate-400">
                出场条件 ({dna.logic_genes.exit_logic})
              </span>
              <div className="mt-1 flex flex-col gap-0.5 rounded-lg border border-slate-700/30 p-2">
                {dna.signal_genes
                  .filter(
                    (g) =>
                      g.role === "exit_trigger" ||
                      g.role === "exit_guard"
                  )
                  .map((gene, gi) => (
                    <div key={gi} className="text-xs text-slate-400">
                      {renderCondition(gene)}
                    </div>
                  ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Cross layer logic */}
      {hasLayers && (dna.layers as TimeframeLayerModel[]).length > 1 && (
        <div className="text-xs text-slate-500">
          跨层逻辑: <span className="font-medium text-slate-400">{dna.cross_layer_logic ?? "AND"}</span>
        </div>
      )}

      {/* Risk genes */}
      <div className="flex items-center gap-4 text-xs text-slate-500">
        <span>
          止损 {(dna.risk_genes.stop_loss * 100).toFixed(1)}%
        </span>
        {dna.risk_genes.take_profit != null && (
          <span>
            止盈 {(dna.risk_genes.take_profit * 100).toFixed(1)}%
          </span>
        )}
        <span>
          仓位 {(dna.risk_genes.position_size * 100).toFixed(0)}%
        </span>
        {(dna.risk_genes.leverage ?? 1) > 1 && (
          <span className="text-amber-500">
            {(dna.risk_genes.leverage ?? 1)}x
          </span>
        )}
        <span>
          {dna.risk_genes.direction === "short"
            ? "做空"
            : dna.risk_genes.direction === "mixed"
              ? "混合"
              : "做多"}
        </span>
      </div>

      {/* Execution */}
      <div className="text-xs text-slate-500">
        执行: {dna.execution_genes.symbol} / {dna.execution_genes.timeframe.toUpperCase()}
      </div>

      {/* Real backtest metrics */}
      {metrics && (
        <div className="mt-1 rounded-lg border border-slate-700/30 p-3">
          <div className="mb-2 text-xs font-medium text-slate-300">
            回测指标
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            <MetricItem
              label="年化收益率"
              value={`${(metrics.annual_return * 100).toFixed(1)}%`}
              score={dimScores?.annual_return}
              positive={metrics.annual_return > 0}
            />
            <MetricItem
              label="夏普比率"
              value={metrics.sharpe_ratio.toFixed(2)}
              score={dimScores?.sharpe_ratio}
              positive={metrics.sharpe_ratio > 0}
            />
            <MetricItem
              label="最大回撤"
              value={`${(metrics.max_drawdown * 100).toFixed(1)}%`}
              score={dimScores?.max_drawdown}
              positive={false}
            />
            <MetricItem
              label="胜率"
              value={`${(metrics.win_rate * 100).toFixed(1)}%`}
              score={dimScores?.win_rate}
              positive={metrics.win_rate > 0.5}
            />
            <MetricItem
              label="Calmar比率"
              value={metrics.calmar_ratio.toFixed(2)}
              score={dimScores?.calmar_ratio}
              positive={metrics.calmar_ratio > 0}
            />
            <MetricItem
              label="交易次数"
              value={`${metrics.total_trades}`}
              score={dimScores?.total_trades}
              positive={metrics.total_trades > 0}
            />
          </div>
        </div>
      )}

      {/* Per-dimension satisfaction details (replaces dimension_scores) */}
      {satisfactionEntries && satisfactionEntries.length > 0 && (
        <div className="mt-1 rounded-lg border border-slate-700/30 p-3">
          <div className="mb-2 text-xs font-medium text-slate-300">
            达标详情
          </div>
          <div className="flex flex-col gap-1.5">
            {satisfactionEntries.map(([dim, info]) => (
              <div key={dim} className="flex items-center justify-between text-xs">
                <span className="text-slate-500">{dim}</span>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-slate-400">
                    {typeof info.actual === "number"
                      ? info.actual.toFixed(3)
                      : String(info.actual)}
                  </span>
                  <span className="text-slate-600">
                    / {typeof info.required === "number" ? info.required.toFixed(3) : String(info.required)}
                  </span>
                  <span className={cn(
                    "font-mono text-[10px]",
                    info.ratio >= 1.0 ? "text-emerald-400" : "text-red-400/70"
                  )}>
                    {typeof info.ratio === "number" ? info.ratio.toFixed(2) : String(info.ratio)}
                  </span>
                  <Badge
                    variant="outline"
                    className={cn(
                      "text-[9px] px-1 py-0",
                      info.met
                        ? "border-emerald-400/30 text-emerald-400"
                        : "border-red-400/30 text-red-400/70"
                    )}
                  >
                    {info.met ? "达标" : "未达标"}
                  </Badge>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MetricItem({
  label,
  value,
  score,
  positive,
}: {
  label: string;
  value: string;
  score?: number;
  positive: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className={positive ? "text-emerald-400" : "text-red-400"}>
          {value}
        </span>
        {score != null && (
          <span className="text-[10px] text-slate-600">
            ({score.toFixed(1)})
          </span>
        )}
      </div>
    </div>
  );
}
