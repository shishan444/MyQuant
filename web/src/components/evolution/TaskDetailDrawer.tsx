import { useState } from "react";
import { X, Eye } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button as AntButton, Tag } from "antd";
import { cn, formatNumber, formatDuration } from "@/lib/utils";
import { TIMEFRAME_LABELS, SCORE_TEMPLATE_LABELS, STOP_REASON_LABELS } from "@/lib/constants";
import { StrategyDetail } from "@/components/StrategyDetail";
import { ScoreTrendChart } from "@/components/evolution/ScoreTrendChart";
import {
  useEvolutionTask,
  useEvolutionHistory,
} from "@/hooks/useEvolution";
import { getTaskStrategies } from "@/services/evolution";
import type { EvolutionTask, DNA } from "@/types/api";

interface TaskDetailDrawerProps {
  taskId: string;
  open: boolean;
  onClose: () => void;
  onSeedEvolve: (dna: DNA) => void;
  onVisualVerify: (dna: DNA, task: EvolutionTask) => void;
}

type TabKey = "overview" | "curve" | "snapshots";

export function TaskDetailDrawer({
  taskId,
  open,
  onClose,
  onSeedEvolve,
  onVisualVerify,
}: TaskDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("overview");

  const { data: task } = useQuery(useEvolutionTask(taskId));
  const { data: historyData } = useQuery(useEvolutionHistory(taskId));
  const { data: strategiesData } = useQuery({
    queryKey: ["evolution", "task-strategies", taskId],
    queryFn: () => getTaskStrategies(taskId),
    enabled: open,
  });

  if (!open || !task) return null;

  const historyRecords = historyData?.records ?? [];
  const strategies = strategiesData?.strategies ?? [];
  const championDna = task.champion_dna;

  const tabs: { key: TabKey; label: string }[] = [
    { key: "overview", label: "概览" },
    { key: "curve", label: "进化曲线" },
    { key: "snapshots", label: "种群快照" },
  ];

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/50"
        onClick={onClose}
      />

      {/* Drawer */}
      <div className="fixed right-0 top-0 z-50 flex h-full w-[560px] max-w-[90vw] flex-col border-l border-slate-700/50 bg-slate-900/95 backdrop-blur-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-700/30 px-5 py-4">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-medium text-slate-200">
              任务详情
            </h3>
            <Tag color={task.status === "completed" ? "success" : "default"} className="!m-0 !text-[10px]">
              {task.status === "completed" ? "已完成" : task.status}
            </Tag>
          </div>
          <AntButton type="text" size="small" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </AntButton>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700/30">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              className={cn(
                "flex-1 px-4 py-2.5 text-xs font-medium transition-colors",
                activeTab === tab.key
                  ? "border-b-2 border-amber-400 text-amber-400"
                  : "text-slate-500 hover:text-slate-300"
              )}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          {activeTab === "overview" && (
            <OverviewTab task={task} championDna={championDna} onSeedEvolve={onSeedEvolve} onVisualVerify={onVisualVerify} />
          )}
          {activeTab === "curve" && (
            <CurveTab records={historyRecords} targetScore={task.target_score} targetFitness={task.best_fitness != null ? task.target_score : undefined} />
          )}
          {activeTab === "snapshots" && (
            <SnapshotsTab strategies={strategies} />
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Tab: Overview
// ---------------------------------------------------------------------------

function OverviewTab({
  task,
  championDna,
  onSeedEvolve,
  onVisualVerify,
}: {
  task: EvolutionTask;
  championDna?: DNA;
  onSeedEvolve: (dna: DNA) => void;
  onVisualVerify: (dna: DNA, task: EvolutionTask) => void;
}) {
  const configItems = [
    { label: "交易对", value: task.symbol },
    { label: "周期", value: task.timeframe_pool?.join("+") ?? TIMEFRAME_LABELS[task.timeframe] ?? task.timeframe },
    { label: "杠杆", value: `${task.leverage}x` },
    { label: "方向", value: task.direction === "short" ? "做空" : task.direction === "mixed" ? "混合" : "做多" },
    { label: "种群大小", value: String(task.population_size) },
  ];

  // Build requirements display items from task data
  const req = task.requirements;
  const requirementItems = req ? [
    { label: "最低年化收益", value: `${(req.min_annual_return * 100).toFixed(0)}%` },
    { label: "最大回撤", value: `${(req.max_drawdown * 100).toFixed(0)}%` },
    { label: "最低胜率", value: `${(req.min_win_rate * 100).toFixed(0)}%` },
    { label: "最低交易次数", value: String(req.min_total_trades) },
    { label: "最低盈亏比", value: req.min_profit_factor.toFixed(1) },
  ] : null;

  const dataItems = [
    { label: "数据起止", value: task.data_time_start && task.data_time_end
      ? `${task.data_time_start.slice(0, 10)} ~ ${task.data_time_end.slice(0, 10)}`
      : "-" },
    { label: "K线条数", value: task.data_row_count ? task.data_row_count.toLocaleString() : "-" },
  ];

  const resultItems = [
    { label: "综合得分", value: formatNumber(task.best_score ?? 0) },
    { label: "最优适应度", value: task.best_fitness != null ? task.best_fitness.toFixed(2) : "-" },
    ...(task.qualified_count != null && task.qualified_count > 0
      ? [{ label: "达标策略数", value: String(task.qualified_count) }]
      : []),
    { label: "运行代数", value: `${task.current_generation} / ${task.max_generations}` },
    { label: "停止原因", value: STOP_REASON_LABELS[task.stop_reason ?? ""] ?? task.stop_reason ?? "-" },
    { label: "用时", value: formatDuration(task.created_at, task.updated_at) },
  ];

  return (
    <div className="flex flex-col gap-5">
      {/* Config */}
      <Section title="任务配置">
        <div className="grid grid-cols-2 gap-x-6 gap-y-2">
          {configItems.map((item) => (
            <div key={item.label} className="flex items-center justify-between text-xs">
              <span className="text-slate-500">{item.label}</span>
              <span className="text-slate-300">{item.value}</span>
            </div>
          ))}
        </div>
      </Section>

      {/* Requirements (replaces score template) */}
      {requirementItems && (
        <Section title="达标要求">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2">
            {requirementItems.map((item) => (
              <div key={item.label} className="flex items-center justify-between text-xs">
                <span className="text-slate-500">{item.label}</span>
                <span className="text-slate-300">{item.value}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
      {/* Fallback: show score template if no requirements */}
      {!requirementItems && task.score_template && (
        <Section title="评分模板 (旧)">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">模板</span>
              <span className="text-slate-300">{SCORE_TEMPLATE_LABELS[task.score_template] ?? task.score_template}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">目标分数</span>
              <span className="text-slate-300">{task.target_score}</span>
            </div>
          </div>
        </Section>
      )}

      {/* Data range */}
      <Section title="数据范围">
        <div className="grid grid-cols-2 gap-x-6 gap-y-2">
          {dataItems.map((item) => (
            <div key={item.label} className="flex items-center justify-between text-xs">
              <span className="text-slate-500">{item.label}</span>
              <span className="text-slate-300">{item.value}</span>
            </div>
          ))}
        </div>
      </Section>

      {/* Results */}
      <Section title="运行结果">
        <div className="grid grid-cols-2 gap-x-6 gap-y-2">
          {resultItems.map((item) => (
            <div key={item.label} className="flex items-center justify-between text-xs">
              <span className="text-slate-500">{item.label}</span>
              <span className="text-slate-300">{item.value}</span>
            </div>
          ))}
        </div>
      </Section>

      {/* Champion DNA */}
      {championDna && (
        <Section title="最优策略">
          <StrategyDetail
            dna={championDna}
            champion_metrics={task.champion_metrics}
            champion_dimension_scores={task.champion_dimension_scores}
            champion_satisfaction={task.champion_satisfaction}
            champion_fitness={task.best_fitness ?? undefined}
            champion_qualified={task.qualified_count != null && task.qualified_count > 0 ? true : undefined}
          />
          <div className="mt-3 flex items-center gap-2">
            <AntButton
              size="small"
              className="!text-[11px]"
              style={{ color: "#34d399" }}
              onClick={() => onVisualVerify(championDna, task)}
            >
              <Eye className="h-3 w-3" />
              可视化验证
            </AntButton>
            <AntButton
              size="small"
              className="!text-[11px]"
              style={{ color: "#c084fc" }}
              onClick={() => onSeedEvolve(championDna)}
            >
              以此为种子继续进化
            </AntButton>
          </div>
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Evolution Curve
// ---------------------------------------------------------------------------

function CurveTab({
  records,
  targetScore,
  targetFitness,
}: {
  records: Array<{ generation: number; best_score: number; best_fitness?: number; avg_score: number; created_at: string; top3_summary?: string }>;
  targetScore: number;
  targetFitness?: number;
}) {
  if (records.length === 0) {
    return <p className="text-xs text-slate-500">暂无进化曲线数据</p>;
  }

  // Detect champion changes
  let prevBest = -Infinity;
  const enrichedRecords = records.map((r) => {
    const isChampionChange = r.best_score > prevBest;
    if (isChampionChange) prevBest = r.best_score;
    const hasFitness = r.best_fitness != null;

    // Parse diagnostics
    let diag: { fallback_pct?: number; zero_trade_pct?: number; avg_trades?: number } = {};
    if (r.top3_summary) {
      try {
        const match = r.top3_summary.match(/diag=(\{.*\})/);
        if (match) diag = JSON.parse(match[1]);
      } catch { /* ignore */ }
    }

    return { ...r, isChampionChange, hasFitness, diag };
  });

  return (
    <div className="flex flex-col gap-4">
      <ScoreTrendChart records={records} targetScore={targetScore} targetFitness={targetFitness} />

      {/* Stats summary */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-slate-700/30 p-3 text-center">
          <div className="text-[11px] text-slate-500">总代数</div>
          <div className="mt-1 text-sm font-mono text-slate-200">{records.length}</div>
        </div>
        <div className="rounded-lg border border-slate-700/30 p-3 text-center">
          <div className="text-[11px] text-slate-500">最高得分</div>
          <div className="mt-1 text-sm font-mono text-amber-400">
            {formatNumber(Math.max(...records.map((r) => r.best_score)))}
          </div>
        </div>
        <div className="rounded-lg border border-slate-700/30 p-3 text-center">
          <div className="text-[11px] text-slate-500">平均得分</div>
          <div className="mt-1 text-sm font-mono text-slate-300">
            {formatNumber(records.reduce((s, r) => s + r.avg_score, 0) / records.length)}
          </div>
        </div>
      </div>

      {/* Generation details table */}
      <Section title="每代详情">
        <div className="max-h-[300px] overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-slate-900/95">
              <tr className="border-b border-slate-700/30 text-slate-500">
                <th className="py-1.5 text-left font-medium">代数</th>
                <th className="py-1.5 text-right font-medium">最优</th>
                <th className="py-1.5 text-right font-medium">适应度</th>
                <th className="py-1.5 text-right font-medium">平均</th>
                <th className="py-1.5 text-center font-medium">状态</th>
                <th className="py-1.5 text-right font-medium">诊断</th>
              </tr>
            </thead>
            <tbody>
              {enrichedRecords.map((r) => (
                <tr key={r.generation} className="border-b border-slate-700/10">
                  <td className="py-1.5 text-slate-300">Gen {r.generation}</td>
                  <td className="py-1.5 text-right font-mono text-amber-400">
                    {formatNumber(r.best_score)}
                  </td>
                  <td className="py-1.5 text-right font-mono">
                    {r.hasFitness ? (
                      <span className={r.best_fitness! >= (targetFitness ?? 1.0) ? "text-emerald-400" : "text-slate-400"}>
                        {r.best_fitness!.toFixed(2)}
                      </span>
                    ) : (
                      <span className="text-slate-600">-</span>
                    )}
                  </td>
                  <td className="py-1.5 text-right font-mono text-slate-400">
                    {formatNumber(r.avg_score)}
                  </td>
                  <td className="py-1.5 text-center">
                    {r.isChampionChange ? (
                      <span className="text-emerald-400">策略更新</span>
                    ) : (
                      <span className="text-slate-600">-</span>
                    )}
                  </td>
                  <td className="py-1.5 text-right text-slate-600">
                    {r.diag.avg_trades != null ? `${r.diag.avg_trades}t` : ""}
                    {r.diag.fallback_pct != null && r.diag.fallback_pct > 0 && (
                      <span className="text-red-400/70 ml-1">F{r.diag.fallback_pct}%</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Population Snapshots
// ---------------------------------------------------------------------------

function SnapshotsTab({
  strategies,
}: {
  strategies: Array<{
    strategy_id: string;
    dna: DNA;
    source: string;
    generation?: number;
    score: number;
  }>;
}) {
  if (strategies.length === 0) {
    return <p className="text-xs text-slate-500">暂无种群快照数据</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {strategies.map((s, idx) => (
        <div
          key={s.strategy_id || idx}
          className="rounded-lg border border-slate-700/30 p-3"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Tag
                color={s.source === "champion" ? "warning" : "default"}
                className="!m-0 !text-[10px]"
              >
                {s.source === "champion" ? "最优" : `Gen ${s.generation ?? "?"}`}
              </Tag>
              <span className="text-xs font-mono text-slate-300">
                {formatNumber(s.score)}
              </span>
            </div>
            <StrategyDetail dna={s.dna} className="hidden" />
          </div>
          <div className="mt-2">
            <StrategyDetail dna={s.dna} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section helper
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-medium text-slate-400">{title}</h4>
      {children}
    </div>
  );
}
