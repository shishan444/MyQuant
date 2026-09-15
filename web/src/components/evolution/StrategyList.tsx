import { useMemo, useCallback, memo } from "react";
import { Eye, Dna, Play, Save } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/badge";
import { cn, formatPercent, formatNumber, getFitnessColor } from "@/lib/utils";
import { getStrategyName, getStrategyType } from "@/lib/strategy-utils";
import type { DiscoveredStrategy, DNA } from "@/types/api";

interface StrategyListProps {
  strategies: DiscoveredStrategy[];
  expandedId: string | null;
  onToggleExpand: (strategyId: string) => void;
  onSeedEvolve: (dna: DNA) => void;
  onSave?: (strategy: DiscoveredStrategy) => void;
  onVisualVerify?: (strategy: DiscoveredStrategy) => void;
}

function formatIndicatorLabel(gene: {
  indicator: string;
  params?: Record<string, unknown>;
}): string {
  const period = gene.params?.period;
  if (period) return `${gene.indicator}(${period})`;
  return gene.indicator;
}

function getDnaIndicators(dna: DNA | null | undefined): string {
  if (!dna) return "-";
  if (dna.layers && dna.layers.length > 0) {
    return dna.layers
      .flatMap((l) => l.signal_genes)
      .map(formatIndicatorLabel)
      .join("+");
  }
  return dna.signal_genes.map(formatIndicatorLabel).join("+");
}

function getMtfBadge(dna: DNA | null | undefined): string | null {
  if (!dna?.layers || dna.layers.length <= 1) return null;
  return dna.layers.map((l) => l.timeframe.toUpperCase()).join("+");
}

export function StrategyList({
  strategies,
  expandedId,
  onToggleExpand,
  onSeedEvolve,
  onSave,
  onVisualVerify,
}: StrategyListProps) {
  if (strategies.length === 0) return null;

  return (
    <div className="flex flex-col gap-1">
      {/* Header row */}
      <div className="flex items-center gap-4 px-4 py-2 text-[11px] text-slate-500">
        <span className="w-7 shrink-0 text-center">排名</span>
        <span className="flex-1">策略</span>
        <span className="w-20 shrink-0 text-right">年化收益</span>
        <span className="w-16 shrink-0 text-right">最大回撤</span>
        <span className="w-14 shrink-0 text-right">夏普</span>
        <span className="w-14 shrink-0 text-right">胜率</span>
        <span className="w-12 shrink-0 text-right">交易数</span>
        <span className="w-28 shrink-0 text-right">操作</span>
      </div>

      {strategies.map((strategy, idx) => (
        <StrategyRow
          key={strategy.strategy_id}
          rank={idx + 1}
          strategy={strategy}
          expanded={expandedId === strategy.strategy_id}
          onToggle={() => onToggleExpand(strategy.strategy_id)}
          onSeedEvolve={() => strategy.dna && onSeedEvolve(strategy.dna)}
          onSave={onSave ? () => onSave(strategy) : undefined}
          onVisualVerify={onVisualVerify ? () => onVisualVerify(strategy) : undefined}
        />
      ))}
    </div>
  );
}

interface StrategyRowProps {
  rank: number;
  strategy: DiscoveredStrategy;
  expanded: boolean;
  onToggle: () => void;
  onSeedEvolve: () => void;
  onSave?: () => void;
  onVisualVerify?: () => void;
}

const StrategyRow = memo(function StrategyRow({
  rank,
  strategy,
  expanded,
  onToggle,
  onSeedEvolve,
  onSave,
  onVisualVerify,
}: StrategyRowProps) {
  const m = strategy.metrics;
  const mtfBadge = useMemo(() => getMtfBadge(strategy.dna), [strategy.dna]);
  const strategyName = useMemo(
    () => strategy.name || getStrategyName(strategy.dna),
    [strategy.name, strategy.dna]
  );
  const strategyType = useMemo(() => getStrategyType(strategy.dna), [strategy.dna]);
  const indicators = useMemo(
    () => getDnaIndicators(strategy.dna),
    [strategy.dna]
  );

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-4 rounded-lg px-4 py-3 transition-colors hover:bg-white/[0.03]",
          rank <= 3 && "ring-1 ring-amber-400/20 bg-amber-400/[0.03]"
        )}
      >
        {/* Rank */}
        <span
          className={cn(
            "w-7 shrink-0 text-center font-mono text-sm font-semibold",
            rank === 1
              ? "text-amber-400"
              : rank <= 3
                ? "text-amber-400/70"
                : "text-slate-500"
          )}
        >
          #{rank}
        </span>

        {/* Strategy info */}
        <div className="flex flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-1.5">
            {strategy.qualified && (
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 shrink-0" title="达标" />
            )}
            <Badge
              variant="outline"
              className={cn(
                "border-slate-700/50 px-1.5 py-0 text-[10px]",
                strategyType === "趋势"
                  ? "text-blue-400"
                  : strategyType === "动量"
                    ? "text-amber-400"
                    : strategyType === "波动"
                      ? "text-purple-400"
                      : "text-slate-400"
              )}
            >
              {strategyType}
            </Badge>
            {mtfBadge && (
              <Badge
                variant="outline"
                className="border-purple-400/30 px-1 py-0 text-[10px] text-purple-400"
              >
                {mtfBadge}
              </Badge>
            )}
            <span className="truncate text-sm text-slate-100">
              {strategyName}
            </span>
          </div>
          <span className="truncate text-xs text-slate-500">
            {indicators}
          </span>
        </div>

        {/* Annual return */}
        <MetricValue
          className="w-20"
          value={m ? formatPercent(m.annual_return) : "-"}
          positive={m ? m.annual_return > 0 : undefined}
        />

        {/* Max drawdown */}
        <span className="w-16 shrink-0 text-right font-mono text-[13px] text-red-400">
          {m ? formatPercent(m.max_drawdown) : "-"}
        </span>

        {/* Sharpe ratio */}
        <MetricValue
          className="w-14"
          value={m ? formatNumber(m.sharpe_ratio) : "-"}
          positive={m ? m.sharpe_ratio > 0 : undefined}
        />

        {/* Win rate */}
        <MetricValue
          className="w-14"
          value={m ? `${(m.win_rate * 100).toFixed(1)}%` : "-"}
          positive={m ? m.win_rate > 0.5 : undefined}
        />

        {/* Trade count */}
        <span className="w-12 shrink-0 text-right font-mono text-[13px] text-slate-400">
          {m?.total_trades ?? "-"}
        </span>

        {/* Actions */}
        <div className="flex w-28 shrink-0 items-center justify-end gap-0.5">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onToggle}
            aria-label="查看详情"
          >
            <Eye className="h-3.5 w-3.5" />
          </Button>
          {onVisualVerify && (
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-emerald-400"
              onClick={onVisualVerify}
              aria-label="回测"
            >
              <Play className="h-3.5 w-3.5" />
            </Button>
          )}
          {onSave && (
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-amber-400"
              onClick={onSave}
              aria-label="保存"
            >
              <Save className="h-3.5 w-3.5" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-purple-400"
            onClick={onSeedEvolve}
            aria-label="继续进化"
          >
            <Dna className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && strategy.dna && (
        <StrategyDetail dna={strategy.dna} strategy={strategy} onClose={onToggle} />
      )}
    </div>
  );
});

function MetricValue({
  className,
  value,
  positive,
}: {
  className: string;
  value: string;
  positive?: boolean;
}) {
  return (
    <span
      className={cn(
        `${className} shrink-0 text-right font-mono text-[13px]`,
        positive === undefined
          ? "text-slate-500"
          : positive
            ? "text-emerald-400"
            : "text-red-400"
      )}
    >
      {value}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Expanded detail panel
// ---------------------------------------------------------------------------

interface StrategyDetailProps {
  dna: DNA;
  strategy: DiscoveredStrategy;
  onClose: () => void;
}

function StrategyDetail({ dna, strategy, onClose }: StrategyDetailProps) {
  const m = strategy.metrics;

  const renderCondition = useCallback(
    (gene: { indicator: string; params?: Record<string, unknown>; condition: { type: string; value?: number } }) => {
      const condLabel: Record<string, string> = {
        lt: "<",
        gt: ">",
        le: "<=",
        ge: ">=",
        cross_above: "金叉",
        cross_below: "死叉",
        price_above: "价格在上方",
        price_below: "价格在下方",
      };
      const label = condLabel[gene.condition.type] ?? gene.condition.type;
      const val =
        gene.condition.value != null ? ` ${gene.condition.value}` : "";
      const period = gene.params?.period;
      const periodStr = period ? `(${period})` : "";
      return `${gene.indicator}${periodStr} ${label}${val}`;
    },
    []
  );

  const entryGenes = dna.layers
    ? dna.layers.flatMap((l) =>
        l.signal_genes.filter(
          (g) => g.role === "entry_trigger" || g.role === "entry_guard"
        )
      )
    : dna.signal_genes.filter(
        (g) => g.role === "entry_trigger" || g.role === "entry_guard"
      );

  const exitGenes = dna.layers
    ? dna.layers.flatMap((l) =>
        l.signal_genes.filter(
          (g) => g.role === "exit_trigger" || g.role === "exit_guard"
        )
      )
    : dna.signal_genes.filter(
        (g) => g.role === "exit_trigger" || g.role === "exit_guard"
      );

  const hasExitGenes = exitGenes.length > 0;

  return (
    <div className="rounded-lg border border-slate-700/30 bg-white/[0.02] px-4 py-3 ml-7 mr-0 mb-2">
      {/* Layers or flat genes */}
      {dna.layers && dna.layers.length > 0 ? (
        dna.layers.map((layer, idx) => {
          const roleLabel =
            idx === 0 && dna.layers!.length > 1
              ? "趋势过滤"
              : idx === dna.layers!.length - 1 && dna.layers!.length > 1
                ? "入场信号"
                : dna.layers!.length > 2
                  ? "确认信号"
                  : undefined;
          return (
            <div key={idx} className="mb-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-medium text-slate-300">
                  Layer {idx + 1} - {layer.timeframe.toUpperCase()}
                </span>
                {roleLabel && (
                  <Badge
                    variant="outline"
                    className="border-slate-700/50 text-[10px] text-slate-500"
                  >
                    {roleLabel}
                  </Badge>
                )}
              </div>
              <div className="flex flex-col gap-1 rounded-lg border border-slate-700/30 p-2">
                {layer.signal_genes.map((gene, gi) => (
                  <div
                    key={gi}
                    className="flex items-center gap-2 text-xs text-slate-400"
                  >
                    <span className="font-mono text-slate-300">
                      {gene.indicator}
                      {gene.params?.period
                        ? `(${gene.params.period})`
                        : ""}
                    </span>
                    <span className="text-slate-600">
                      {gene.condition.type}
                    </span>
                    {gene.condition.value != null && (
                      <span className="font-mono text-slate-300">
                        {gene.condition.value}
                      </span>
                    )}
                  </div>
                ))}
              </div>
              {idx < dna.layers!.length - 1 && (
                <div className="my-2 flex items-center gap-2 text-xs text-slate-600">
                  <span className="font-medium">
                    {dna.cross_layer_logic ?? "AND"}
                  </span>
                </div>
              )}
            </div>
          );
        })
      ) : (
        <>
          {/* Entry conditions */}
          <div className="mb-2">
            <span className="text-xs font-medium text-slate-400">
              入场条件 ({dna.logic_genes.entry_logic})
            </span>
            <div className="mt-1 flex flex-col gap-1 rounded-lg border border-slate-700/30 p-2">
              {entryGenes.length > 0 ? (
                entryGenes.map((gene, gi) => (
                  <div key={gi} className="text-xs text-slate-400">
                    {renderCondition(gene)}
                  </div>
                ))
              ) : (
                dna.signal_genes.map((gene, gi) => (
                  <div key={gi} className="text-xs text-slate-400">
                    {renderCondition(gene)}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Exit conditions */}
          {hasExitGenes && (
            <div className="mb-2">
              <span className="text-xs font-medium text-slate-400">
                出场条件 ({dna.logic_genes.exit_logic})
              </span>
              <div className="mt-1 flex flex-col gap-1 rounded-lg border border-slate-700/30 p-2">
                {exitGenes.map((gene, gi) => (
                  <div key={gi} className="text-xs text-slate-400">
                    {renderCondition(gene)}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Risk genes */}
      <div className="mb-2 flex items-center gap-4 text-xs text-slate-500">
        <span>
          风控: 止损 {(dna.risk_genes.stop_loss * 100).toFixed(1)}%
        </span>
        {dna.risk_genes.take_profit != null && (
          <span>
            止盈 {(dna.risk_genes.take_profit * 100).toFixed(1)}%
          </span>
        )}
        <span>
          仓位 {(dna.risk_genes.position_size * 100).toFixed(0)}%
        </span>
      </div>

      {/* Execution */}
      <div className="mb-2 text-xs text-slate-500">
        执行: {dna.execution_genes.symbol} /{" "}
        {dna.execution_genes.timeframe.toUpperCase()}
      </div>

      {/* Metrics summary */}
      {m ? (
        <div className="rounded-lg border border-slate-700/30 p-2">
          <div className="mb-1.5 text-xs font-medium text-slate-300">
            回测指标
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            <DetailMetric
              label="年化收益率"
              value={formatPercent(m.annual_return)}
              positive={m.annual_return > 0}
            />
            <DetailMetric
              label="夏普比率"
              value={m.sharpe_ratio.toFixed(2)}
              positive={m.sharpe_ratio > 0}
            />
            <DetailMetric
              label="最大回撤"
              value={formatPercent(m.max_drawdown)}
              positive={false}
            />
            <DetailMetric
              label="胜率"
              value={`${(m.win_rate * 100).toFixed(1)}%`}
              positive={m.win_rate > 0.5}
            />
            <DetailMetric
              label="交易次数"
              value={`${m.total_trades}`}
              positive
            />
            <DetailMetric
              label="盈亏比"
              value={m.profit_factor.toFixed(2)}
              positive={m.profit_factor > 1}
            />
          </div>
          <div className="mt-1.5 text-[10px] text-slate-600">
            综合得分: <span className="font-mono text-emerald-400">{strategy.score.toFixed(1)}</span>
            {" "} / 代数: <span className="font-mono text-slate-300">G{strategy.generation}</span>
            {strategy.fitness != null && (
              <>
                {" "} / 适应度:{" "}
                <span className={`font-mono ${strategy.qualified ? "text-emerald-400" : getFitnessColor(strategy.fitness ?? 0)}`}>
                  {strategy.fitness.toFixed(2)}
                </span>
              </>
            )}
            {strategy.qualified && (
              <Badge
                variant="outline"
                className="ml-2 border-emerald-400/30 text-[9px] text-emerald-400 px-1 py-0"
              >
                达标
              </Badge>
            )}
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span>
            综合得分:{" "}
            <span className="font-mono font-semibold text-emerald-400">
              {strategy.score.toFixed(1)}
            </span>
          </span>
          <span>代数: G{strategy.generation}</span>
        </div>
      )}

      {/* Actions */}
      <div className="mt-3 flex items-center gap-2">
        <Button
          variant="ghost"
          size="xs"
          className="text-slate-500"
          onClick={onClose}
        >
          收起
        </Button>
      </div>
    </div>
  );
}

function DetailMetric({
  label,
  value,
  positive,
}: {
  label: string;
  value: string;
  positive: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-500">{label}</span>
      <span className={positive ? "text-emerald-400" : "text-red-400"}>
        {value}
      </span>
    </div>
  );
}
