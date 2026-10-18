import { useState, useMemo, useCallback, useEffect } from "react";
import { useSearchParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { motion, AnimatePresence } from "motion/react";
import {
  BookOpen, Play, Pencil, Trash2, Search,
  Dna, FlaskConical, Zap, ArrowUpDown, ArrowUp, ArrowDown, ChevronRight,
  Copy, Check, X, GitBranch, LineChart, AlertCircle,
} from "lucide-react";
import { PageTransition } from "@/components/PageTransition";
import { EmptyState } from "@/components/EmptyState";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button as AntButton, Input, Modal, Form, Select as AntSelect } from "antd";
import { useStrategies, useDeleteStrategy, useUpdateStrategy } from "@/hooks/useStrategies";
import { deleteStrategy } from "@/services/strategies";
import { toast } from "sonner";
import { cn, getFitnessColor } from "@/lib/utils";
import { INDICATOR_LABELS, CONDITION_TYPE_LABELS } from "@/lib/constants";
import type { Strategy } from "@/types/api";

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function fmtReturn(value: number | undefined | null): string {
  if (value == null) return "--";
  const pct = value * 100;
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

function fmtDrawdown(value: number | undefined | null): string {
  if (value == null) return "--";
  return `${(value * 100).toFixed(1)}%`;
}

function fmtWinRate(value: number | undefined | null): string {
  if (value == null) return "--";
  return `${(value * 100).toFixed(0)}%`;
}

function fmtPF(value: number | undefined | null): string {
  if (value == null) return "--";
  return value.toFixed(2);
}

// ---------------------------------------------------------------------------
// Source badge
// ---------------------------------------------------------------------------

const SOURCE_STYLES: Record<string, { bg: string; text: string; icon: React.ElementType }> = {
  evolution: { bg: "bg-accent-purple/15", text: "text-accent-purple", icon: Dna },
  lab: { bg: "bg-accent-gold/15", text: "text-accent-gold", icon: FlaskConical },
  manual: { bg: "bg-accent-gold/15", text: "text-accent-gold", icon: FlaskConical },
  import: { bg: "bg-info/15", text: "text-info", icon: BookOpen },
};

function SourceBadge({ source }: { source: string }) {
  const style = SOURCE_STYLES[source] ?? SOURCE_STYLES.manual;
  const Icon = style.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium", style.bg, style.text)}>
      <Icon className="h-2.5 w-2.5" />
      {source === "evolution" ? "进化" : source === "lab" ? "实验室" : source === "import" ? "导入" : source}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog
// ---------------------------------------------------------------------------

function EditStrategyDialog({
  open,
  strategy,
  onClose,
}: {
  open: boolean;
  strategy: Strategy | null;
  onClose: () => void;
}) {
  const updateMutation = useUpdateStrategy();
  // 表单草稿唯一持有者=AntD Form（ui-state spec；失败保留草稿）
  const [form] = Form.useForm<{ name: string; tags: string; notes: string }>();

  // 打开时同步策略当前值到表单草稿
  useEffect(() => {
    if (open && strategy) {
      form.setFieldsValue({ name: strategy.name ?? "", tags: strategy.tags ?? "", notes: strategy.notes ?? "" });
    }
  }, [open, strategy, form]);

  const handleCancel = () => onClose();

  if (!strategy) return null;

  const handleSave = () => {
    const values = form.getFieldsValue();
    updateMutation.mutate(
      { id: strategy.strategy_id, payload: { name: values.name || undefined, tags: values.tags || undefined, notes: values.notes || undefined } },
      { onSuccess: onClose },
    );
  };

  return (
    <Modal
      open={open}
      onCancel={handleCancel}
      destroyOnHidden
      title="编辑策略"
      width={440}
      footer={[
        <AntButton key="cancel" onClick={handleCancel}>
          取消
        </AntButton>,
        <AntButton key="save" type="primary" loading={updateMutation.isPending} onClick={handleSave}>
          保存
        </AntButton>,
      ]}
    >
      <Form form={form} layout="vertical" className="!mb-0 pt-2">
        <Form.Item label="名称" name="name" className="!mb-3">
          <Input placeholder="策略名称" />
        </Form.Item>
        <Form.Item label="标签" name="tags" className="!mb-3">
          <Input placeholder="用逗号分隔" />
        </Form.Item>
        <Form.Item label="备注" name="notes" className="!mb-0">
          <Input.TextArea placeholder="策略备注..." rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Sort helpers
// ---------------------------------------------------------------------------

type SortField = "verify_star" | "name" | "annual_return" | "max_drawdown" | "win_rate" | "profit_factor" | "total_trades" | "best_fitness" | "created_at";
type SortDir = "asc" | "desc";

const METRIC_SORT_ACCESSORS: Record<SortField, (s: Strategy) => string | number> = {
  verify_star: (s) => (s.verify_star ?? 0) * 10000 + (s.best_fitness ?? 0),
  name: (s) => (s.name ?? s.strategy_id).toLowerCase(),
  annual_return: (s) => s.metrics?.annual_return ?? -Infinity,
  max_drawdown: (s) => s.metrics?.max_drawdown ?? -Infinity,
  win_rate: (s) => s.metrics?.win_rate ?? -Infinity,
  profit_factor: (s) => s.metrics?.profit_factor ?? -Infinity,
  total_trades: (s) => s.metrics?.total_trades ?? -Infinity,
  best_fitness: (s) => s.best_fitness ?? -Infinity,
  created_at: (s) => new Date(s.created_at).getTime(),
};

function SortIcon({ field, currentSort }: { field: SortField; currentSort: { field: SortField; dir: SortDir } }) {
  if (currentSort.field !== field) return <ArrowUpDown className="h-2.5 w-2.5 opacity-30" />;
  if (currentSort.dir === "asc") return <ArrowUp className="h-2.5 w-2.5 text-accent-gold" />;
  return <ArrowDown className="h-2.5 w-2.5 text-accent-gold" />;
}

// ---------------------------------------------------------------------------
// DNA condition text
// ---------------------------------------------------------------------------

function describeGene(gene: { indicator: string; params: Record<string, unknown>; role: string; condition?: { type: string; threshold?: number; ref_indicator?: string } }): string {
  const label = INDICATOR_LABELS[gene.indicator] ?? gene.indicator;
  const paramStr = Object.values(gene.params).join(",");
  const cond = gene.condition;
  const condLabel = cond ? (CONDITION_TYPE_LABELS[cond.type] ?? cond.type) : "";
  const condValue = cond?.threshold != null ? ` ${cond.threshold}` : cond?.ref_indicator ? ` ${cond.ref_indicator}` : "";
  const roleMap: Record<string, string> = {
    entry_trigger: "入场触发", entry_guard: "入场条件",
    exit_trigger: "出场触发", exit_guard: "出场条件",
    add_trigger: "加仓触发", add_guard: "加仓条件",
    reduce_trigger: "减仓触发", reduce_guard: "减仓条件",
    direction: "方向",
  };
  const roleLabel = roleMap[gene.role] ?? gene.role;
  return `${roleLabel}: ${label}(${paramStr}) ${condLabel}${condValue}`;
}

// ---------------------------------------------------------------------------
// Row expand panel
// ---------------------------------------------------------------------------

function ExpandPanel({ strategy, onRun, onPaperTrade, onSeedEvolve }: {
  strategy: Strategy;
  onRun: () => void;
  onPaperTrade: () => void;
  onSeedEvolve: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const m = strategy.metrics;
  const dna = strategy.dna;
  const rg = dna?.risk_genes;

  const handleCopy = () => {
    if (!dna) return;
    navigator.clipboard.writeText(JSON.stringify(dna, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.25, ease: [0.25, 0.46, 0.45, 0.94] }}
      className="overflow-hidden"
    >
      <div className="ml-10 mr-4 mb-2 rounded-lg border border-border-default/30 bg-white/[0.01] p-4">
        <div className="grid grid-cols-3 gap-6">
          {/* Left: Strategy info */}
          <div className="flex flex-col gap-2 text-xs">
            <div className="text-[11px] text-text-muted font-medium mb-1">策略信息</div>
            <InfoRow label="交易对" value={strategy.symbol} />
            <InfoRow label="周期" value={strategy.timeframe} />
            <InfoRow label="方向" value={rg?.direction === "long" ? "做多" : rg?.direction === "short" ? "做空" : "混合"} />
            <InfoRow label="杠杆" value={rg?.leverage ? `${rg.leverage}x` : "--"} valueClass="text-amber-400" />
            <InfoRow label="来源" value={<SourceBadge source={strategy.source} />} />
            {strategy.generation > 0 && <InfoRow label="进化代数" value={`Gen ${strategy.generation}`} />}
            <InfoRow label="创建时间" value={new Date(strategy.created_at).toLocaleString()} valueClass="text-text-muted" />
            {strategy.tags && <InfoRow label="标签" value={strategy.tags} valueClass="text-text-muted" />}
            {strategy.notes && <InfoRow label="备注" value={strategy.notes} valueClass="text-text-muted" />}
          </div>

          {/* Middle: Backtest metrics */}
          <div className="flex flex-col gap-3">
            <div className="text-[11px] text-text-muted font-medium">回测指标</div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
              <MetricItem label="年化收益率" value={fmtReturn(m?.annual_return)} positive={m?.annual_return != null && m.annual_return > 0} />
              <MetricItem label="夏普比率" value={m?.sharpe_ratio?.toFixed(2) ?? "--"} positive={m?.sharpe_ratio != null && m.sharpe_ratio > 1} />
              <MetricItem label="最大回撤" value={fmtDrawdown(m?.max_drawdown)} positive={false} />
              <MetricItem label="胜率" value={fmtWinRate(m?.win_rate)} positive={m?.win_rate != null && m.win_rate > 0.5} />
              <MetricItem label="交易次数" value={m?.total_trades?.toString() ?? "--"} />
              <MetricItem label="盈亏比" value={fmtPF(m?.profit_factor)} positive={m?.profit_factor != null && m.profit_factor > 1} />
              <MetricItem label="Calmar比率" value={m?.calmar_ratio?.toFixed(2) ?? "--"} />
            </div>

            {strategy.best_fitness != null && (
              <div className="mt-2 pt-2 border-t border-border-default/30">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-text-muted">适应度:</span>
                  <span className={cn("font-mono text-sm", strategy.qualified ? "text-emerald-400" : getFitnessColor(strategy.best_fitness ?? 0))}>
                    {strategy.best_fitness.toFixed(2)}
                  </span>
                  {strategy.qualified && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-emerald-400/15 text-emerald-400">达标</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Right: DNA structure + actions */}
          <div className="flex flex-col gap-3">
            <div className="text-[11px] text-text-muted font-medium">策略结构</div>
            {dna?.signal_genes?.length ? (
              <div className="flex flex-col gap-1 text-xs">
                {dna.signal_genes.slice(0, 6).map((gene, i) => (
                  <div key={i} className="text-text-secondary font-mono leading-relaxed truncate">
                    {describeGene(gene)}
                  </div>
                ))}
                {dna.signal_genes.length > 6 && (
                  <div className="text-text-muted text-[11px]">...+{dna.signal_genes.length - 6} 条件</div>
                )}
              </div>
            ) : (
              <div className="text-xs text-text-muted">无 DNA 数据</div>
            )}

            {rg && (
              <div className="flex items-center gap-3 text-xs text-text-muted mt-1">
                {rg.stop_loss != null && <span>止损 {(rg.stop_loss * 100).toFixed(1)}%</span>}
                {rg.take_profit != null && <span>止盈 {(rg.take_profit * 100).toFixed(1)}%</span>}
                {rg.position_size != null && <span>仓位 {(rg.position_size * 100).toFixed(0)}%</span>}
              </div>
            )}

            <div className="flex flex-wrap gap-2 mt-3">
              <AntButton type="text" size="small" className="!text-xs" style={{ color: "#00C853" }} onClick={onRun}>
                <Play className="h-3 w-3" /> 运行回测
              </AntButton>
              <AntButton type="text" size="small" className="!text-xs" style={{ color: "#00b37a" }} onClick={onPaperTrade}>
                <Zap className="h-3 w-3" /> 模拟交易
              </AntButton>
              <AntButton type="text" size="small" className="!text-xs" style={{ color: "#8B5CF6" }} onClick={onSeedEvolve}>
                <GitBranch className="h-3 w-3" /> 继续进化
              </AntButton>
              <AntButton type="text" size="small" className="!text-xs !text-text-muted" onClick={handleCopy}>
                {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                {copied ? "已复制" : "复制DNA"}
              </AntButton>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function InfoRow({ label, value, valueClass }: { label: string; value: React.ReactNode; valueClass?: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-text-muted w-14 shrink-0">{label}</span>
      <span className={cn("text-text-secondary", valueClass)}>{value}</span>
    </div>
  );
}

function MetricItem({ label, value, positive }: { label: string; value: string; positive?: boolean | null }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-text-muted">{label}</span>
      <span className={cn(
        "font-mono tabular-nums",
        positive === true && "text-emerald-400",
        positive === false && "text-red-400",
        positive == null && "text-text-secondary",
      )}>
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Strategy card row
// ---------------------------------------------------------------------------

const ROW_EASE: [number, number, number, number] = [0.25, 0.46, 0.45, 0.94];

const rowVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.03, duration: 0.3, ease: ROW_EASE },
  }),
  exit: { opacity: 0, y: -8, transition: { duration: 0.2 } },
};

interface StrategyRowProps {
  strategy: Strategy;
  index: number;
  selected: boolean;
  expanded: boolean;
  onToggleSelect: () => void;
  onToggleExpand: () => void;
  onDelete: () => void;
  onEdit: () => void;
  onRun: () => void;
  onPaperTrade: () => void;
  onSeedEvolve: () => void;
}

function StrategyCardRow({
  strategy, index, selected, expanded,
  onToggleSelect, onToggleExpand, onDelete, onEdit, onRun, onPaperTrade, onSeedEvolve,
}: StrategyRowProps) {
  const m = strategy.metrics;
  const annualReturn = m?.annual_return;
  const returnTrend = annualReturn != null ? (annualReturn > 0 ? "up" : annualReturn < 0 ? "down" : "neutral") : "neutral";

  return (
    <>
      <motion.tr
        custom={index}
        variants={rowVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
        layout
        className={cn(
          "group relative border-b border-border-default/50 transition-all duration-200 hover:bg-white/[0.03]",
          (strategy.verify_star ?? 0) > 0 && "border-l-2 border-l-amber-400/20",
          expanded && "bg-white/[0.02]",
        )}
        style={{ originY: 0 }}
      >
        {/* Checkbox */}
        <td className="py-3 pl-3 pr-1 w-8">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleSelect}
            onClick={(e) => e.stopPropagation()}
            className="h-3.5 w-3.5 rounded border-border-default/60 bg-transparent accent-accent-gold cursor-pointer"
          />
        </td>

        {/* Star rating */}
        <td className="py-3 px-1 text-center">
          {(strategy.verify_star ?? 0) > 0 ? (
            <span className={cn(
              "text-xs font-mono font-semibold",
              strategy.verify_star === 5 ? "text-amber-400" :
              (strategy.verify_star ?? 0) >= 3 ? "text-blue-400" :
              "text-slate-400"
            )}>
              ★{strategy.verify_star}
            </span>
          ) : (strategy.verify_count ?? 0) > 0 ? (
            <span className="text-xs text-slate-600">--</span>
          ) : null}
        </td>

        {/* Name */}
        <td className="py-3 px-2 cursor-pointer" onClick={onToggleExpand}>
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="flex items-center gap-2">
              <ChevronRight className={cn("h-3 w-3 text-text-muted/40 transition-transform duration-200 shrink-0", expanded && "rotate-90")} />
              <span className="text-sm font-medium text-text-primary truncate">
                {strategy.name ?? strategy.strategy_id.slice(0, 8)}
              </span>
              <SourceBadge source={strategy.source} />
            </div>
          </div>
        </td>

        {/* Annual return */}
        <td className="py-3 px-2">
          <span className={cn(
            "font-mono text-[13px] font-semibold tabular-nums tracking-tight",
            returnTrend === "up" && "text-profit",
            returnTrend === "down" && "text-loss",
            returnTrend === "neutral" && "text-text-secondary",
          )}>
            {fmtReturn(annualReturn)}
          </span>
        </td>

        {/* Drawdown */}
        <td className="py-3 px-2">
          <span className="font-mono text-[13px] tabular-nums text-loss/80">{fmtDrawdown(m?.max_drawdown)}</span>
        </td>

        {/* Win rate */}
        <td className="py-3 px-2">
          <span className={cn(
            "font-mono text-[13px] tabular-nums",
            m?.win_rate != null && m.win_rate > 0.5 ? "text-profit/80" : "text-text-secondary",
          )}>
            {fmtWinRate(m?.win_rate)}
          </span>
        </td>

        {/* Profit factor */}
        <td className="py-3 px-2">
          <span className={cn(
            "font-mono text-[13px] tabular-nums",
            m?.profit_factor != null && m.profit_factor > 1 ? "text-profit/80" : "text-text-secondary",
          )}>
            {fmtPF(m?.profit_factor)}
          </span>
        </td>

        {/* Total trades */}
        <td className="py-3 px-2">
          <span className="font-mono text-[13px] tabular-nums text-text-secondary">
            {m?.total_trades ?? "--"}
          </span>
        </td>

        {/* Data */}
        <td className="py-3 px-2">
          <span className="text-xs text-text-muted font-mono">
            {strategy.symbol} <span className="text-text-muted/40">/</span> {strategy.timeframe}
          </span>
        </td>

        {/* Date */}
        <td className="py-3 px-2">
          <span className="font-mono text-xs text-text-muted/60 tabular-nums">
            {new Date(strategy.created_at).toLocaleDateString()}
          </span>
        </td>

        {/* Actions */}
        <td className="py-3 pr-4 text-right w-28">
          <div className="flex items-center justify-end gap-0.5 opacity-0 group-hover:opacity-100 translate-x-2 group-hover:translate-x-0 transition-all duration-200">
            <AntButton type="text" size="small" aria-label="运行回测" onClick={onRun} style={{ color: "#00b37a" }}>
              <Play className="h-3.5 w-3.5" />
            </AntButton>
            <AntButton type="text" size="small" aria-label="模拟交易" onClick={onPaperTrade} style={{ color: "#c78d1e" }}>
              <Zap className="h-3.5 w-3.5" />
            </AntButton>
            <AntButton type="text" size="small" aria-label="编辑策略" onClick={onEdit} className="!text-text-muted">
              <Pencil className="h-3.5 w-3.5" />
            </AntButton>
            <AntButton type="text" size="small" danger aria-label="删除策略" onClick={onDelete}>
              <Trash2 className="h-3.5 w-3.5" />
            </AntButton>
          </div>
        </td>
      </motion.tr>

      {/* Expand panel */}
      <AnimatePresence>
        {expanded && (
          <tr>
            <td colSpan={10} className="p-0 border-b border-border-default/30">
              <ExpandPanel
                strategy={strategy}
                onRun={onRun}
                onPaperTrade={onPaperTrade}
                onSeedEvolve={onSeedEvolve}
              />
            </td>
          </tr>
        )}
      </AnimatePresence>
    </>
  );
}

// ---------------------------------------------------------------------------
// Empty state with animation
// ---------------------------------------------------------------------------

function AnimatedEmptyState({ onNavigate }: { onNavigate: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
      className="flex flex-col items-center justify-center gap-5 py-16"
    >
      <motion.div
        animate={{ y: [0, -6, 0] }}
        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
        className="relative"
      >
        <BookOpen className="h-14 w-14 text-accent-gold/30" />
        <motion.div
          className="absolute inset-0 rounded-full bg-accent-gold/5 blur-xl"
          animate={{ scale: [1, 1.2, 1], opacity: [0.3, 0.6, 0.3] }}
          transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
        />
      </motion.div>
      <div className="flex flex-col items-center gap-2 text-center">
        <h3 className="text-lg font-medium text-text-primary">策略库为空</h3>
        <p className="text-sm text-text-secondary max-w-xs">
          通过策略实验室创建你的第一个策略，或启动进化任务自动发现策略
        </p>
      </div>
      <motion.div whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}>
        <AntButton type="primary" size="large" onClick={onNavigate}>
          <FlaskConical className="h-4 w-4" />
          前往实验室
        </AntButton>
      </motion.div>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <PageTransition>
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <div className="h-6 w-32 animate-pulse rounded bg-white/[0.04]" />
          <div className="ml-auto flex gap-2">
            <div className="h-7 w-44 animate-pulse rounded-md bg-white/[0.04]" />
            <div className="h-7 w-24 animate-pulse rounded-md bg-white/[0.04]" />
            <div className="h-7 w-24 animate-pulse rounded-md bg-white/[0.04]" />
          </div>
        </div>
        <div className="rounded-xl border border-border-default/50 overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3.5 border-b border-border-default/30">
              <div className="h-3.5 w-3.5 rounded animate-pulse bg-white/[0.04]" />
              <div className="h-4 w-32 animate-pulse rounded bg-white/[0.04]" />
              <div className="ml-auto h-4 w-16 animate-pulse rounded bg-white/[0.04]" />
              <div className="h-4 w-12 animate-pulse rounded bg-white/[0.04]" />
              <div className="h-4 w-14 animate-pulse rounded bg-white/[0.04]" />
            </div>
          ))}
        </div>
      </div>
    </PageTransition>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const SORTABLE_COLUMNS: { field: SortField; label: string }[] = [
  { field: "annual_return", label: "年化" },
  { field: "max_drawdown", label: "回撤" },
  { field: "win_rate", label: "胜率" },
  { field: "profit_factor", label: "盈亏比" },
  { field: "total_trades", label: "交易" },
  { field: "created_at", label: "日期" },
];

export function Strategies() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Filters——URL 所有（ui-state spec：筛选刷新可恢复，?q=&source=&verify=）
  const [searchParams, setSearchParams] = useSearchParams();
  const searchQuery = searchParams.get("q") ?? "";
  const sourceFilter = searchParams.get("source") ?? "all";
  const verifyFilter = searchParams.get("verify") ?? "all";

  const setSearchQuery = useCallback((v: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v) next.set("q", v); else next.delete("q");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const setSourceFilter = useCallback((v: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v !== "all") next.set("source", v); else next.delete("source");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const setVerifyFilter = useCallback((v: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v !== "all") next.set("verify", v); else next.delete("verify");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Expand
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Sort (client-side for metric columns, server-side for created_at)
  const [sortState, setSortState] = useState<{ field: SortField; dir: SortDir }>({ field: "verify_star", dir: "desc" });

  // Dialogs
  const [deleteTarget, setDeleteTarget] = useState<Strategy | null>(null);
  const [editTarget, setEditTarget] = useState<Strategy | null>(null);
  const [batchDeleteTargets, setBatchDeleteTargets] = useState<Strategy[]>([]);

  // Data - use best_fitness as default sort
  const { data, isLoading, isError, error, refetch } = useQuery(
    useStrategies({ sort_by: "created_at", sort_order: "desc", limit: 200 })
  );
  const deleteMutation = useDeleteStrategy();

  const allStrategies: Strategy[] = data?.items ?? [];

  // Client-side filtering
  const filteredStrategies = useMemo(() => {
    let result = allStrategies;
    if (sourceFilter !== "all") {
      result = result.filter((s) => s.source === sourceFilter);
    }
    if (verifyFilter === "verified") {
      result = result.filter((s) => (s.verify_count ?? 0) > 0);
    } else if (verifyFilter === "unverified") {
      result = result.filter((s) => (s.verify_count ?? 0) === 0);
    } else if (verifyFilter === "qualified") {
      result = result.filter((s) => s.qualified === true);
    } else if (verifyFilter === "unqualified") {
      result = result.filter((s) => s.qualified === false);
    } else if (verifyFilter === "star5") {
      result = result.filter((s) => (s.verify_star ?? 0) >= 5);
    } else if (verifyFilter === "star4") {
      result = result.filter((s) => (s.verify_star ?? 0) >= 4);
    } else if (verifyFilter === "star3") {
      result = result.filter((s) => (s.verify_star ?? 0) >= 3);
    }
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (s) =>
          s.name?.toLowerCase().includes(query) ||
          s.symbol.toLowerCase().includes(query) ||
          s.strategy_id.toLowerCase().includes(query),
      );
    }
    return result;
  }, [allStrategies, sourceFilter, verifyFilter, searchQuery]);

  // Client-side sorting
  const sortedStrategies = useMemo(() => {
    const accessor = METRIC_SORT_ACCESSORS[sortState.field];
    if (!accessor) return filteredStrategies;
    return [...filteredStrategies].sort((a, b) => {
      const va = accessor(a);
      const vb = accessor(b);
      const cmp = typeof va === "string" && typeof vb === "string"
        ? va.localeCompare(vb)
        : (va as number) - (vb as number);
      return sortState.dir === "asc" ? cmp : -cmp;
    });
  }, [filteredStrategies, sortState]);

  // Selection helpers
  const allSelected = sortedStrategies.length > 0 && selectedIds.size === sortedStrategies.length;
  const toggleAll = () => {
    if (allSelected) setSelectedIds(new Set());
    else setSelectedIds(new Set(sortedStrategies.map((s) => s.strategy_id)));
  };
  const toggleOne = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Sort toggle (two-state: desc ↔ asc)
  const cycleSort = (field: SortField) => {
    setSortState((prev) => {
      if (prev.field !== field) return { field, dir: "desc" };
      return { field, dir: prev.dir === "desc" ? "asc" : "desc" };
    });
  };

  // Actions
  const handleDelete = (id: string) => {
    deleteMutation.mutate(id, { onSuccess: () => setDeleteTarget(null) });
  };

  /** 批量删除：部分失败不静默——呈现成功/失败计数，选中态与缓存始终清理。 */
  const handleBatchDelete = async () => {
    const ids = Array.from(selectedIds);
    const results = await Promise.allSettled(ids.map((id) => deleteStrategy(id)));
    const failedCount = results.filter((r) => r.status === "rejected").length;

    setSelectedIds(new Set());
    setBatchDeleteTargets([]);
    queryClient.invalidateQueries({ queryKey: ["strategies"] });

    if (failedCount > 0) {
      toast.error(`批量删除：成功 ${ids.length - failedCount} 个，失败 ${failedCount} 个`);
    } else {
      toast.success(`已删除 ${ids.length} 个策略`);
    }
  };

  const handleRun = (strategy: Strategy) => {
    if (!strategy.dna) return;
    navigate("/lab", { state: { dna: strategy.dna, symbol: strategy.symbol, timeframe: strategy.timeframe } });
  };

  const handlePaperTrade = (strategy: Strategy) => {
    // 上盘流转（rebuild-paper-trading）：Trading 创建面只收 strategy_id，DNA 由后端从策略库快照
    navigate("/trading", { state: { strategyId: strategy.strategy_id } });
  };

  const handleSeedEvolve = (strategy: Strategy) => {
    if (!strategy.dna) return;
    navigate("/evolution", {
      state: { seedDna: strategy.dna, symbol: strategy.symbol, timeframe: strategy.timeframe },
    });
  };

  // --- Loading ---
  if (isLoading) return <LoadingSkeleton />;

  // --- Error ---
  if (isError) {
    return (
      <PageTransition>
        <EmptyState
          icon={AlertCircle}
          title="加载失败"
          description={(error as Error)?.message || "无法加载策略列表，请重试"}
          actions={[{ label: "重试", onClick: () => refetch() }]}
        />
      </PageTransition>
    );
  }

  // --- Empty ---
  if (allStrategies.length === 0) {
    return (
      <PageTransition>
        <AnimatedEmptyState onNavigate={() => navigate("/lab")} />
      </PageTransition>
    );
  }

  // --- Main ---
  return (
    <PageTransition>
      <div className="flex flex-col gap-4">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-text-secondary">已保存策略</span>
            <span className="font-mono text-xs text-accent-gold bg-accent-gold/10 rounded px-1.5 py-0.5 tabular-nums">
              {allStrategies.length}
            </span>
          </div>

          {/* Batch actions */}
          {selectedIds.size > 0 && !allSelected && sortedStrategies.length > 0 && (
            <AntButton
              type="text"
              size="small"
              className="!text-xs"
              style={{ color: "#F59E0B" }}
              onClick={() => setSelectedIds(new Set(sortedStrategies.map((s) => s.strategy_id)))}
            >
              全选当前筛选 ({sortedStrategies.length})
            </AntButton>
          )}
          {selectedIds.size > 0 && (
            <motion.div
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-center gap-2 bg-white/[0.03] rounded-lg px-3 py-1"
            >
              <span className="text-sm text-text-secondary">已选 {selectedIds.size} 项</span>
              <AntButton
                size="small"
                className="!text-xs"
                icon={<LineChart className="h-3 w-3" />}
                onClick={() => {
                  navigate("/batch-backtest", { state: { strategy_ids: Array.from(selectedIds) } });
                }}
              >
                批量回测
              </AntButton>
              <AntButton
                size="small"
                danger
                className="!text-xs"
                icon={<Trash2 className="h-3 w-3" />}
                onClick={() => {
                  const selected = sortedStrategies.filter((s) => selectedIds.has(s.strategy_id));
                  setBatchDeleteTargets(selected);
                }}
              >
                批量删除
              </AntButton>
              <AntButton type="text" size="small" className="!text-xs" onClick={() => setSelectedIds(new Set())}>
                <X className="h-3 w-3 mr-1" /> 取消选择
              </AntButton>
            </motion.div>
          )}

          <div className="ml-auto flex items-center gap-2">
            <Input
              size="small"
              placeholder="搜索策略..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              prefix={<Search className="h-3.5 w-3.5 text-text-muted" />}
              className="!w-48 !text-xs"
              allowClear
            />

            <AntSelect
              size="small"
              value={sourceFilter}
              onChange={setSourceFilter}
              className="!w-28 !text-xs"
              aria-label="来源筛选"
              options={[
                { value: "all", label: "全部来源" },
                { value: "lab", label: "实验室" },
                { value: "evolution", label: "进化" },
                { value: "import", label: "导入" },
              ]}
            />

            <AntSelect
              size="small"
              value={verifyFilter}
              onChange={setVerifyFilter}
              className="!w-28 !text-xs"
              aria-label="验证状态筛选"
              options={[
                { value: "all", label: "全部策略" },
                { value: "qualified", label: "达标" },
                { value: "unqualified", label: "未达标" },
                { value: "verified", label: "已验证" },
                { value: "unverified", label: "未验证" },
                { value: "star3", label: "3★ 以上" },
                { value: "star4", label: "4★ 以上" },
                { value: "star5", label: "5★" },
              ]}
            />
          </div>
        </div>

        {/* Strategy table */}
        <div className="rounded-xl border border-border-default/50 overflow-hidden bg-white/[0.01]">
          <table className="w-full text-sm" style={{ tableLayout: "fixed" }}>
            <colgroup>
              <col style={{ width: "32px" }} />
              <col style={{ width: "48px" }} />
              <col style={{ width: "22%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "7%" }} />
              <col style={{ width: "7%" }} />
              <col style={{ width: "6%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "100px" }} />
            </colgroup>
            <thead>
              <tr className="border-b border-border-default/40">
                {/* Checkbox header */}
                <th className="py-2.5 pl-3 pr-1">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="h-3.5 w-3.5 rounded border-border-default/60 bg-transparent accent-accent-gold cursor-pointer"
                  />
                </th>
                {/* Star sort header */}
                <th
                  className="py-2.5 text-center text-xs font-medium uppercase tracking-wider text-text-muted/60 whitespace-nowrap cursor-pointer hover:text-text-muted select-none"
                  onClick={() => cycleSort("verify_star")}
                >
                  <span className="inline-flex items-center justify-center gap-1">
                    星级
                    <SortIcon field="verify_star" currentSort={sortState} />
                  </span>
                </th>
                {/* Name sort */}
                <th
                  className="py-2.5 text-left text-xs font-medium uppercase tracking-wider text-text-muted/60 whitespace-nowrap cursor-pointer hover:text-text-muted select-none"
                  onClick={() => cycleSort("name")}
                >
                  <span className="inline-flex items-center gap-1">
                    名称
                    <SortIcon field="name" currentSort={sortState} />
                  </span>
                </th>
                {/* Sortable metric columns */}
                {SORTABLE_COLUMNS.map(({ field, label }) => (
                  <th
                    key={field}
                    className="py-2.5 text-left text-xs font-medium uppercase tracking-wider text-text-muted/60 whitespace-nowrap cursor-pointer hover:text-text-muted select-none"
                    onClick={() => cycleSort(field)}
                  >
                    <span className="inline-flex items-center gap-1">
                      {label}
                      <SortIcon field={field} currentSort={sortState} />
                    </span>
                  </th>
                ))}
                {/* Data */}
                <th className="py-2.5 text-left text-xs font-medium uppercase tracking-wider text-text-muted/60 whitespace-nowrap">
                  数据
                </th>
                {/* Actions */}
                <th className="py-2.5 text-right pr-4 text-xs font-medium uppercase tracking-wider text-text-muted/60" />
              </tr>
            </thead>
            <tbody>
              <AnimatePresence mode="popLayout">
                {sortedStrategies.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="h-24 text-center text-text-muted text-sm">
                      未找到匹配的策略
                    </td>
                  </tr>
                ) : (
                  sortedStrategies.map((strategy, i) => (
                    <StrategyCardRow
                      key={strategy.strategy_id}
                      strategy={strategy}
                      index={i}
                      selected={selectedIds.has(strategy.strategy_id)}
                      expanded={expandedId === strategy.strategy_id}
                      onToggleSelect={() => toggleOne(strategy.strategy_id)}
                      onToggleExpand={() => setExpandedId((prev) => prev === strategy.strategy_id ? null : strategy.strategy_id)}
                      onDelete={() => setDeleteTarget(strategy)}
                      onEdit={() => setEditTarget(strategy)}
                      onRun={() => handleRun(strategy)}
                      onPaperTrade={() => handlePaperTrade(strategy)}
                      onSeedEvolve={() => handleSeedEvolve(strategy)}
                    />
                  ))
                )}
              </AnimatePresence>
            </tbody>
          </table>
        </div>

        {/* Single delete confirm */}
        <ConfirmDialog
          open={deleteTarget !== null}
          onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}
          title="删除策略"
          description={`确定要删除策略「${deleteTarget?.name ?? deleteTarget?.strategy_id}」吗？此操作不可撤销。`}
          confirmLabel="删除"
          variant="destructive"
          onConfirm={() => deleteTarget && handleDelete(deleteTarget.strategy_id)}
          loading={deleteMutation.isPending}
        />

        {/* Batch delete confirm */}
        <ConfirmDialog
          open={batchDeleteTargets.length > 0}
          onOpenChange={(open) => { if (!open) setBatchDeleteTargets([]); }}
          title="批量删除策略"
          description={`确定要删除 ${batchDeleteTargets.length} 个策略吗？此操作不可撤销。`}
          confirmLabel="删除全部"
          variant="destructive"
          onConfirm={handleBatchDelete}
        />

        {/* Edit dialog */}
        <EditStrategyDialog
          open={editTarget !== null}
          strategy={editTarget}
          onClose={() => setEditTarget(null)}
        />
      </div>
    </PageTransition>
  );
}
