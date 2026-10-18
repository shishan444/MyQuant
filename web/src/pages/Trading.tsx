import { useState, useEffect, useCallback, useMemo } from "react";
import { useLocation, useNavigate } from "react-router";
import {
  TrendingUp,
  Square,
  Trash2,
  Plus,
  Copy,
  Search,
  AlertCircle,
  AlertTriangle,
} from "lucide-react";
import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { EmptyState } from "@/components/EmptyState";
import { App, Button as AntButton, Collapse, Input, Table, Tabs, Tag, type TableProps } from "antd";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import {
  useTradingTasks,
  useTradingFills,
  useStopTradingTask,
  useCreateTradingTask,
  useCloneTradingTask,
  useDeleteTradingTask,
  useTradingWebSocket,
  useTradingEquity,
  useTradingReport,
  useTradingFinalReport,
  useTradingFunding,
  useRunnerStatus,
  isTerminalStatus,
} from "@/hooks/useTrading";
import { TradingChart } from "@/components/trading/TradingChart";
import { EquityCurve } from "@/components/charts/EquityCurve";
import { MetricsDashboard } from "@/components/trading/MetricsDashboard";
import { RunnerStatusBadge } from "@/components/trading/RunnerStatusBadge";
import { CreateTaskDialog } from "@/components/trading/CreateTaskDialog";
import type { PaperFill, FundingRecord, TradingTask } from "@/services/trading";

type PositionSide = "long" | "short" | "flat";

/** 五态徽标（pending/running/completed/stopped/error——新域状态机） */
const STATUS_BADGE_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "排队中", color: "warning" },
  running: { label: "运行中", color: "success" },
  completed: { label: "已完结", color: "processing" },
  stopped: { label: "已停止", color: "error" },
  error: { label: "异常", color: "error" },
};

const POSITION_BADGE_MAP: Record<PositionSide, { label: string; color: string }> = {
  long: { label: "做多", color: "success" },
  short: { label: "做空", color: "error" },
  flat: { label: "空仓", color: "default" },
};

function toPositionSide(side: string | null | undefined): PositionSide {
  if (side === "long") return "long";
  if (side === "short") return "short";
  return "flat";
}

/** 策略编号短显（UUID 形制取前 8 位；非 UUID 原样） */
function shortStrategyId(strategyId: string): string {
  return strategyId.length > 12 ? `${strategyId.slice(0, 8)}…` : strategyId;
}

// ---------------------------------------------------------------------------
// Task List Item (sidebar)
// ---------------------------------------------------------------------------

function TaskListItem({
  task,
  isSelected,
  onSelect,
  onDelete,
  onClone,
}: {
  task: TradingTask;
  isSelected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onClone: () => void;
}) {
  const statusBadge = STATUS_BADGE_MAP[task.status] ?? STATUS_BADGE_MAP.stopped;

  const returnRate =
    task.initial_cash > 0 ? (task.total_pnl || 0) / task.initial_cash : 0;
  // clone/delete 终态门槛：运行/排队中不可删不可克隆（后端 409 stop first）
  const isTerminal = isTerminalStatus(task.status);

  return (
    <GlassCard
      className={cn(
        "flex flex-col gap-1.5 cursor-pointer transition-all p-3",
        isSelected && "ring-1 ring-accent-gold/30",
      )}
      hover={false}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between">
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-sm font-medium text-text-primary truncate">
            {task.symbol}
          </span>
          <span className="text-[10px] text-text-muted" title={task.strategy_id}>
            {shortStrategyId(task.strategy_id)} / {task.timeframe} / {task.leverage}x
          </span>
        </div>
        <Tag color={statusBadge.color} className="!m-0 !text-[9px] !leading-4 shrink-0">
          {statusBadge.label}
        </Tag>
      </div>
      <div className="flex items-end justify-between">
        <span className="font-num text-sm font-semibold text-text-primary">
          {formatCurrency(task.balance ?? task.initial_cash)}
        </span>
        <span
          className={cn(
            "font-num text-xs font-medium",
            returnRate > 0 && "text-profit",
            returnRate < 0 && "text-loss",
            returnRate === 0 && "text-text-muted",
          )}
        >
          {formatPercent(returnRate)}
        </span>
      </div>
      {isTerminal && (
        <div className="flex items-center gap-1 self-end">
          <AntButton
            type="text"
            size="small"
            aria-label="克隆重跑"
            onClick={(e) => {
              e.stopPropagation();
              onClone();
            }}
          >
            <Copy className="h-3 w-3" style={{ color: "#F59E0B" }} />
          </AntButton>
          <AntButton
            type="text"
            size="small"
            danger
            aria-label="删除任务"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            <Trash2 className="h-3 w-3" />
          </AntButton>
        </div>
      )}
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export function Trading() {
  const { modal } = App.useApp();
  const { data: taskList, isLoading, isError, error, refetch } = useTradingTasks();
  const { data: runnerStatus } = useRunnerStatus();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const location = useLocation();
  const navigate = useNavigate();
  const createTask = useCreateTradingTask();
  const cloneMut = useCloneTradingTask();
  const deleteMut = useDeleteTradingTask();

  const tasks = taskList?.tasks ?? [];
  const activeTaskId =
    selectedTaskId ?? tasks.find((t) => t.status === "running")?.task_id ?? null;

  const wsConnected = useTradingWebSocket(activeTaskId);

  // Filter tasks by search query
  const filteredTasks = useMemo(() => {
    if (!searchQuery.trim()) return tasks;
    const q = searchQuery.toLowerCase();
    return tasks.filter(
      (t) =>
        t.symbol.toLowerCase().includes(q) ||
        t.strategy_id.toLowerCase().includes(q),
    );
  }, [tasks, searchQuery]);

  // Route state for auto-create from Strategies/Verify pages（上盘流转：带 strategy_id）
  const [presetStrategyId, setPresetStrategyId] = useState<string | null>(null);

  useEffect(() => {
    const state = location.state as { strategyId?: unknown } | null;
    if (state?.strategyId && typeof state.strategyId === "string") {
      setPresetStrategyId(state.strategyId);
      setDialogOpen(true);
      window.history.replaceState({}, "");
    }
  }, []);

  const handleConfirmCreate = useCallback(
    (params: Parameters<typeof createTask.mutate>[0]) => {
      createTask.mutate(params);
      setPresetStrategyId(null);
      setDialogOpen(false);
    },
    [createTask],
  );

  const openDeleteConfirm = useCallback((taskId: string) => {
    modal.confirm({
      title: "删除任务",
      content: "将永久删除该任务及所有关联数据（成交、权益、资金费、报告），此操作不可撤销。",
      okText: "确认删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => {
        deleteMut.mutate(taskId);
        if (selectedTaskId === taskId) setSelectedTaskId(null);
      },
    });
  }, [modal, deleteMut, selectedTaskId]);

  const openCloneConfirm = useCallback((task: TradingTask) => {
    modal.confirm({
      title: "克隆重跑",
      content: `以同一策略快照与任务参数（${task.symbol} / ${task.timeframe}，初始资金 ${formatCurrency(task.initial_cash)}）创建新任务？原任务数据保留。`,
      okText: "创建克隆任务",
      cancelText: "取消",
      onOk: () => {
        cloneMut.mutate(task.task_id);
      },
    });
  }, [modal, cloneMut]);

  if (isLoading) {
    return (
      <PageTransition>
        <div className="flex items-center justify-center h-64">
          <span className="text-text-muted">加载中...</span>
        </div>
      </PageTransition>
    );
  }

  if (isError) {
    return (
      <PageTransition>
        <EmptyState
          icon={AlertCircle}
          title="加载失败"
          description={(error as Error)?.message || "无法加载交易任务，请重试"}
          actions={[{ label: "重试", onClick: () => refetch() }]}
        />
      </PageTransition>
    );
  }

  if (tasks.length === 0) {
    return (
      <PageTransition>
        <EmptyState
          icon={TrendingUp}
          title="暂无模拟交易任务"
          description="前往策略库选择策略，开始模拟交易验证。"
          actions={[
            {
              label: "前往策略库",
              onClick: () => navigate("/strategies"),
              variant: "outline",
            },
          ]}
        />
        <CreateTaskDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          presetStrategyId={presetStrategyId}
          onConfirm={handleConfirmCreate}
        />
      </PageTransition>
    );
  }

  const activeTask = tasks.find((t) => t.task_id === activeTaskId);

  return (
    <PageTransition>
      <div className="flex h-full gap-4">
        {/* Left sidebar: task list */}
        <div className="w-64 flex-shrink-0 flex flex-col gap-2 overflow-y-auto pr-1">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-primary">任务列表</h2>
            <RunnerStatusBadge status={runnerStatus} />
          </div>

          <AntButton
            block
            size="small"
            icon={<Plus className="h-3 w-3" />}
            onClick={() => {
              setPresetStrategyId(null);
              setDialogOpen(true);
            }}
          >
            新建任务
          </AntButton>

          {tasks.length > 3 && (
            <Input
              size="small"
              prefix={<Search className="h-3 w-3 text-text-muted" />}
              placeholder="搜索交易对或策略编号..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              allowClear
            />
          )}

          {filteredTasks.map((task) => (
            <TaskListItem
              key={task.task_id}
              task={task}
              isSelected={task.task_id === activeTaskId}
              onSelect={() => setSelectedTaskId(task.task_id)}
              onDelete={() => openDeleteConfirm(task.task_id)}
              onClone={() => openCloneConfirm(task)}
            />
          ))}
        </div>

        {/* Right panel: detail */}
        <div className="flex-1 flex flex-col gap-3 overflow-y-auto min-h-0">
          {activeTask ? (
            <TaskDetailPanel
              task={activeTask}
              wsConnected={wsConnected}
              onClone={() => openCloneConfirm(activeTask)}
            />
          ) : (
            <div className="flex items-center justify-center h-full">
              <span className="text-sm text-text-muted">请从左侧选择一个任务</span>
            </div>
          )}
        </div>
      </div>

      {/* Create dialog */}
      <CreateTaskDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        presetStrategyId={presetStrategyId}
        onConfirm={handleConfirmCreate}
      />

      {/* 确认交互单源=AntD modal 体系；删除/克隆弹层由 openDeleteConfirm/openCloneConfirm 命令式触发 */}
    </PageTransition>
  );
}

// ---------------------------------------------------------------------------
// Task Detail Panel (right side)
// ---------------------------------------------------------------------------

const FILL_PAGE_SIZE = 5;

function TaskDetailPanel({
  task,
  wsConnected,
  onClone,
}: {
  task: TradingTask;
  wsConnected: boolean;
  onClone: () => void;
}) {
  const stopMut = useStopTradingTask();

  const { data: fillData } = useTradingFills(task.task_id, 500);
  const { data: equityData } = useTradingEquity(task.task_id, 500);
  const { data: fundingData } = useTradingFunding(task.task_id, 500);
  const { data: reportData } = useTradingReport(task.task_id);
  const isTerminal = isTerminalStatus(task.status);
  const { data: finalReportData } = useTradingFinalReport(task.task_id, isTerminal);

  const fills = fillData?.fills ?? [];
  const totalTrades = task.total_trades;
  const equitySnapshots = equityData?.snapshots ?? [];
  const fundingRecords = fundingData?.records ?? [];
  const isRunning = task.status === "running";
  const isActive = isRunning || task.status === "pending";
  const mutating = stopMut.isPending;

  const [visibleFills, setVisibleFills] = useState(FILL_PAGE_SIZE);

  // Reset visible count when task changes
  useEffect(() => {
    setVisibleFills(FILL_PAGE_SIZE);
  }, [task.task_id]);

  const displayedFills = fills.slice(0, visibleFills);
  const hasMoreFills = visibleFills < fills.length || (fillData?.has_more ?? false);

  // Account overview computations
  const posSide = toPositionSide(task.position_side);
  const hasPosition = posSide !== "flat";
  const upnl = task.unrealized_pnl ?? 0;
  const equity = hasPosition
    ? (task.balance ?? 0) + (task.position_margin ?? 0) + upnl
    : task.balance ?? 0;
  const equityReturn =
    task.initial_cash > 0 ? (equity - task.initial_cash) / task.initial_cash : 0;

  // 报告源：终态取结题固化版（含版本标识），否则滚动报告
  const report = finalReportData?.report ?? reportData;
  const reportBadge = finalReportData
    ? `结题固化版 v${finalReportData.report_version}`
    : isTerminal
      ? "结题固化版生成中"
      : "滚动报告";

  return (
    <div className="flex flex-col gap-3">
      {/* Account Overview Card */}
      <GlassCard className="p-4">
        {/* Header: strategy info + controls */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-semibold text-text-primary">
              {task.symbol}
            </h2>
            <span
              className="text-xs text-text-muted"
              title={task.strategy_id}
            >
              策略 {shortStrategyId(task.strategy_id)} / {task.timeframe} / {task.leverage}x
            </span>
            <Tag color={STATUS_BADGE_MAP[task.status]?.color} className="!m-0 !text-[10px]">
              {STATUS_BADGE_MAP[task.status]?.label ?? task.status}
            </Tag>
            {hasPosition && (
              <Tag color={POSITION_BADGE_MAP[posSide].color} className="!m-0 !text-[10px]">
                {POSITION_BADGE_MAP[posSide].label}
              </Tag>
            )}
            {wsConnected && isRunning && (
              <span
                className="h-2 w-2 rounded-full bg-profit animate-pulse"
                title="实时连接"
              />
            )}
          </div>
          <div className="flex items-center gap-2">
            {isActive && (
              <AntButton
                type="text"
                size="small"
                danger
                disabled={mutating}
                onClick={() => stopMut.mutate(task.task_id)}
              >
                <Square className="h-3 w-3" /> 停止
              </AntButton>
            )}
            {isTerminal && (
              <AntButton size="small" onClick={onClone}>
                <Copy className="h-3 w-3" /> 克隆重跑
              </AntButton>
            )}
          </div>
        </div>

        {/* Error detail line（error 终态/装载失败的拒绝原因披露） */}
        {task.status === "error" && task.error_detail && (
          <div className="mt-2 flex items-start gap-2 rounded border border-loss/30 bg-loss/10 px-2 py-1.5 text-xs text-loss">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span className="break-all">{task.error_detail}</span>
          </div>
        )}
        {task.status === "stopped" && task.stop_reason && (
          <div className="mt-1 text-xs text-text-muted">停止原因：{task.stop_reason}</div>
        )}

        {/* Three key numbers: Balance / Unrealized PnL / Equity */}
        <div className="mt-3 grid grid-cols-3 gap-6">
          <div>
            <span className="text-[10px] text-text-muted uppercase tracking-wider">
              账户余额
            </span>
            <p className="font-num text-lg font-semibold text-text-primary">
              {formatCurrency(task.balance ?? 0)}
            </p>
            <span
              className={cn(
                "font-num text-xs",
                equityReturn > 0 && "text-profit",
                equityReturn < 0 && "text-loss",
                equityReturn === 0 && "text-text-muted",
              )}
            >
              {formatPercent(equityReturn)}
            </span>
          </div>
          <div>
            <span className="text-[10px] text-text-muted uppercase tracking-wider">
              未实现盈亏
            </span>
            <p
              className={cn(
                "font-num text-lg font-semibold",
                upnl > 0 && "text-profit",
                upnl < 0 && "text-loss",
                upnl === 0 && "text-text-primary",
              )}
            >
              {formatCurrency(upnl)}
            </p>
          </div>
          <div>
            <span className="text-[10px] text-text-muted uppercase tracking-wider">
              账户权益
            </span>
            <p
              className={cn(
                "font-num text-lg font-semibold",
                equityReturn > 0 && "text-profit",
                equityReturn < 0 && "text-loss",
                equityReturn === 0 && "text-text-primary",
              )}
            >
              {formatCurrency(equity)}
            </p>
          </div>
        </div>

        {/* Position detail line (only when position is open) */}
        {hasPosition && (
          <div className="mt-2 pt-2 border-t border-border-default flex items-center gap-4 text-xs text-text-muted flex-wrap">
            <span>开仓价 {formatCurrency(task.position_entry ?? 0)}</span>
            <span>
              数量 {task.position_quantity?.toFixed(6)}
            </span>
            <span>
              保证金 {formatCurrency(task.position_margin ?? 0)}
            </span>
          </div>
        )}
      </GlassCard>

      {/* Performance Metrics（报告第一层主指标）+ 报告源版本标识 */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-text-secondary">验证报告</h3>
        <Tag className="!m-0 !text-[10px]" color={finalReportData ? "gold" : "default"}>
          {reportBadge}
        </Tag>
      </div>
      <MetricsDashboard report={report} task={task} />

      {/* 报告二三层折叠（偏差对照+证据强度——考场无观点，只披露不判定） */}
      {report && (
        <GlassCard className="p-3">
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: "deviation",
                label: <span className="text-xs text-text-secondary">偏差对照层（纸盘 vs 同区间回测预期）</span>,
                children: <DeviationLayer deviation={report.deviation} />,
              },
              {
                key: "evidence",
                label: <span className="text-xs text-text-secondary">证据强度层（60/5 双阈值）</span>,
                children: <EvidenceLayer evidence={report.evidence} />,
              },
            ]}
          />
        </GlassCard>
      )}

      {/* Chart + Equity Curve (grouped in one card) */}
      <GlassCard className="p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-text-secondary">K线图</span>
          <div className="flex items-center gap-3 text-[10px] text-text-muted">
            <span className="text-profit">&#9650; 开多/加仓</span>
            <span className="text-loss">&#9660; 平仓/止损</span>
          </div>
        </div>
        <TradingChart
          symbol={task.symbol}
          timeframe={task.timeframe}
          fills={fills}
          height={380}
          isActive={isActive}
        />
        {equitySnapshots.length > 0 && (
          <div className="mt-2 pt-2 border-t border-border-default">
            <span className="text-xs text-text-secondary mb-1 block">权益曲线</span>
            <EquityCurve
              data={equitySnapshots.map((s) => ({ timestamp: s.bar_time, value: s.equity }))}
              baseline={task.initial_cash}
              height={120}
            />
          </div>
        )}
      </GlassCard>

      {/* 成交流水 / 资金费 标签页 */}
      <div>
        <Tabs
          size="small"
          items={[
            {
              key: "fills",
              label: `成交流水 (${totalTrades})`,
              children: (
                <div>
                  {fills.length === 0 ? (
                    <div className="flex items-center justify-center h-20 rounded-lg border border-border-default">
                      <span className="text-sm text-text-muted">暂无成交记录</span>
                    </div>
                  ) : (
                    <div className="rounded-lg border border-border-default overflow-hidden">
                      <Table<PaperFill>
                        rowKey="id"
                        columns={FILL_COLUMNS}
                        dataSource={displayedFills}
                        size="small"
                        pagination={false}
                      />
                      {hasMoreFills && (
                        <div className="flex justify-center py-2 border-t border-border-default">
                          <AntButton
                            type="text"
                            size="small"
                            className="!text-xs !text-text-muted"
                            onClick={() => setVisibleFills(fills.length)}
                          >
                            查看全部
                          </AntButton>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ),
            },
            {
              key: "funding",
              label: `资金费 (${fundingRecords.length})`,
              children: (
                <div>
                  {fundingRecords.length === 0 ? (
                    <div className="flex items-center justify-center h-20 rounded-lg border border-border-default">
                      <span className="text-sm text-text-muted">暂无资金费记录</span>
                    </div>
                  ) : (
                    <div className="rounded-lg border border-border-default overflow-hidden">
                      <Table<FundingRecord>
                        rowKey="id"
                        columns={FUNDING_COLUMNS}
                        dataSource={fundingRecords}
                        size="small"
                        pagination={false}
                        scroll={{ y: 320 }}
                      />
                    </div>
                  )}
                </div>
              ),
            },
          ]}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 报告二三层（折叠内容）
// ---------------------------------------------------------------------------

function fmtNum(v: number | null | undefined, digits = 4): string {
  return v == null ? "-" : v.toFixed(digits);
}

/** 偏差对照层：diff 关键四指标差值（纸盘−回测）+归因披露 */
function DeviationLayer({ deviation }: { deviation: NonNullable<Parameters<typeof MetricsDashboard>[0]["report"]>["deviation"] }) {
  if (deviation.unavailable_reason) {
    return (
      <div className="text-xs text-text-muted space-y-1">
        <p>对照不可用：{deviation.unavailable_reason}</p>
        <ul className="list-disc pl-4 space-y-0.5">
          {deviation.attribution_notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </div>
    );
  }
  const diffs: Array<{ label: string; value: number | null; percent?: boolean }> = [
    { label: "总收益差", value: deviation.diff.total_return, percent: true },
    { label: "Sharpe 差", value: deviation.diff.sharpe_ratio },
    { label: "最大回撤差", value: deviation.diff.max_drawdown, percent: true },
    { label: "胜率差", value: deviation.diff.win_rate, percent: true },
  ];
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        {diffs.map((d) => (
          <div key={d.label} className="rounded border border-border-default px-2 py-1.5">
            <span className="text-[10px] text-text-muted block">{d.label}（纸盘−回测）</span>
            <span
              className={cn(
                "font-num text-sm",
                (d.value ?? 0) > 0 && "text-profit",
                (d.value ?? 0) < 0 && "text-loss",
                d.value == null && "text-text-muted",
              )}
            >
              {d.value == null ? "-" : d.percent ? formatPercent(d.value) : d.value.toFixed(4)}
            </span>
          </div>
        ))}
      </div>
      <ul className="list-disc pl-4 text-xs text-text-muted space-y-0.5">
        {deviation.attribution_notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

/** 证据强度层：样本量+双阈值判定+研究状态快照+funding 三态计数 */
function EvidenceLayer({ evidence }: { evidence: NonNullable<Parameters<typeof MetricsDashboard>[0]["report"]>["evidence"] }) {
  const items = [
    { label: "运行 bar 数", value: `${evidence.bars_run}` },
    { label: "平仓笔数", value: `${evidence.closed_trades}` },
    { label: "持仓 bar 数", value: `${evidence.position_bars}` },
    { label: "资金费已结", value: `${evidence.funding_settled_points} 点` },
    {
      label: "资金费挂起",
      value: evidence.funding_pending_points == null ? "无数据" : `${evidence.funding_pending_points} 点`,
    },
  ];
  return (
    <div className="space-y-2">
      {evidence.insufficient_evidence && (
        <div className="flex items-center gap-2 rounded border border-accent-gold/30 bg-accent-gold/10 px-2 py-1.5 text-xs text-accent-gold">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>证据不足（运行不足 60 根或平仓不足 5 笔）——结论参考性有限</span>
        </div>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
        {items.map((it) => (
          <div key={it.label} className="rounded border border-border-default px-2 py-1.5">
            <span className="text-[10px] text-text-muted block">{it.label}</span>
            <span className="font-num text-sm text-text-primary">{it.value}</span>
          </div>
        ))}
      </div>
      <div className="text-xs text-text-muted space-y-0.5">
        <p>研究状态快照：{evidence.research_status}</p>
        {evidence.funding_post_terminal_backfill && (
          <p>
            终态后补记：{evidence.funding_post_terminal_backfill.points} 点、合计{" "}
            {fmtNum(evidence.funding_post_terminal_backfill.total_amount, 2)}（未计入账户余额）
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fill / Funding 表列
// ---------------------------------------------------------------------------

const KIND_LABEL: Record<string, string> = {
  open: "开仓",
  close: "平仓",
  add: "加仓",
  reduce: "减仓",
  exit_sl: "止损",
  exit_tp: "止盈",
  exit_max_hold: "持有上限",
  exit_time: "限时出场",
  exit_liquidation: "强平",
};
const SIDE_LABEL: Record<string, string> = {
  long: "做多",
  short: "做空",
  "": "-",
};
const SIDE_COLOR: Record<string, string> = {
  long: "text-profit",
  short: "text-loss",
  "": "",
};
const REASON_LABEL: Record<string, string> = {
  signal: "信号",
  sl: "止损",
  tp: "止盈",
  liquidation: "强平",
  reverse: "反手",
  window: "窗口",
  reduce_full: "减仓平仓",
};

/** 成交流水列（kind 九值词表直出） */
const FILL_COLUMNS: TableProps<PaperFill>["columns"] = [
  {
    title: "时间",
    dataIndex: "bar_time",
    render: (v: string) => (
      <span className="text-xs text-text-muted">{v.slice(0, 19).replace("T", " ")}</span>
    ),
  },
  {
    title: "方向",
    dataIndex: "side",
    render: (v: string | null) => (
      <span className={cn("text-sm", SIDE_COLOR[v ?? ""] ?? "text-text-primary")}>
        {SIDE_LABEL[v ?? ""] ?? v ?? "-"}
      </span>
    ),
  },
  {
    title: "操作",
    dataIndex: "kind",
    render: (v: string) => <span className="text-sm text-text-primary">{KIND_LABEL[v] ?? v}</span>,
  },
  {
    title: "价格",
    dataIndex: "price",
    render: (v: number | null) => (
      <span className="font-num text-sm text-text-primary">{v != null ? formatCurrency(v) : "-"}</span>
    ),
  },
  {
    title: "数量",
    dataIndex: "quantity",
    render: (v: number | null) => (
      <span className="font-num text-sm text-text-primary">{v != null ? v.toFixed(6) : "-"}</span>
    ),
  },
  {
    title: "已实现盈亏",
    dataIndex: "realized_pnl",
    render: (v: number | null) =>
      v !== null ? (
        <span className={cn("font-num text-sm font-medium", v > 0 && "text-profit", v < 0 && "text-loss")}>
          {formatCurrency(v)}
        </span>
      ) : (
        <span className="text-text-muted">-</span>
      ),
  },
  {
    title: "原因",
    dataIndex: "reason",
    render: (v: string | null) => (
      <span className="text-xs text-text-muted">{v ? REASON_LABEL[v] ?? v : "-"}</span>
    ),
  },
];

/** 资金费三态标签 */
const FUNDING_STATUS_TAG: Record<string, { label: string; color: string }> = {
  settled: { label: "已结算", color: "success" },
  pending: { label: "挂起待补", color: "warning" },
  settled_disclosure_only: { label: "终态后补记（未扣款）", color: "default" },
};

const FUNDING_COLUMNS: TableProps<FundingRecord>["columns"] = [
  {
    title: "结算时刻",
    dataIndex: "settle_time",
    render: (v: string) => (
      <span className="text-xs text-text-muted">{v.slice(0, 19).replace("T", " ")}</span>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    render: (v: string) => {
      const tag = FUNDING_STATUS_TAG[v] ?? { label: v, color: "default" };
      return (
        <Tag color={tag.color} className="!m-0 !text-[10px]">
          {tag.label}
        </Tag>
      );
    },
  },
  {
    title: "费率",
    dataIndex: "rate",
    render: (v: number | null) => (
      <span className="font-num text-sm text-text-primary">{fmtNum(v, 6)}</span>
    ),
  },
  {
    title: "金额",
    dataIndex: "amount",
    render: (v: number | null) =>
      v !== null ? (
        <span className={cn("font-num text-sm", v > 0 && "text-loss", v < 0 && "text-profit")}>
          {formatCurrency(v)}
        </span>
      ) : (
        <span className="text-text-muted">-</span>
      ),
  },
  {
    title: "持仓数量",
    dataIndex: "position_quantity",
    render: (v: number | null) => (
      <span className="font-num text-sm text-text-primary">{v != null ? v.toFixed(6) : "-"}</span>
    ),
  },
  {
    title: "结算价",
    dataIndex: "settle_price",
    render: (v: number | null) => (
      <span className="font-num text-sm text-text-primary">{v != null ? formatCurrency(v) : "-"}</span>
    ),
  },
];
