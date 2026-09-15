import { useState, useEffect, useCallback, useMemo } from "react";
import { useLocation, useNavigate } from "react-router";
import {
  TrendingUp,
  Square,
  Trash2,
  Plus,
  RotateCcw,
  Search,
  AlertCircle,
} from "lucide-react";
import { PageTransition } from "@/components/PageTransition";
import { GlassCard } from "@/components/GlassCard";
import { EmptyState } from "@/components/EmptyState";
import { App, Button as AntButton, Input, Table, Tag, type TableProps } from "antd";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import {
  useTradingTasks,
  useTradingTrades,
  useStopTradingTask,
  useCreateTradingTask,
  useRestartTradingTask,
  useDeleteTradingTask,
  useTradingWebSocket,
  useTradingEquity,
  useTradingMetrics,
  useRunnerStatus,
} from "@/hooks/useTrading";
import { TradingChart } from "@/components/trading/TradingChart";
import { EquityCurve } from "@/components/trading/EquityCurve";
import { MetricsDashboard } from "@/components/trading/MetricsDashboard";
import { RunnerStatusBadge } from "@/components/trading/RunnerStatusBadge";
import { CreateTaskDialog } from "@/components/trading/CreateTaskDialog";
import type { TradingTask, PaperTrade } from "@/services/trading";

type PositionSide = "long" | "short" | "flat";

const STATUS_BADGE_MAP: Record<string, { label: string; color: string }> = {
  pending: { label: "等待中", color: "warning" },
  running: { label: "运行中", color: "success" },
  stopped: { label: "已停止", color: "error" },
};

const POSITION_BADGE_MAP: Record<PositionSide, { label: string; color: string }> = {
  long: { label: "做多", color: "success" },
  short: { label: "做空", color: "error" },
  flat: { label: "空仓", color: "default" },
};

const DIR_LABEL: Record<string, string> = {
  long: "仅多",
  short: "仅空",
  mixed: "混合",
};

function toPositionSide(side: string | null): PositionSide {
  if (side === "long") return "long";
  if (side === "short") return "short";
  return "flat";
}

// ---------------------------------------------------------------------------
// Task List Item (sidebar)
// ---------------------------------------------------------------------------

function TaskListItem({
  task,
  isSelected,
  onSelect,
  onDelete,
  onRestart,
}: {
  task: TradingTask;
  isSelected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onRestart: () => void;
}) {
  const statusBadge = STATUS_BADGE_MAP[task.status] ?? STATUS_BADGE_MAP.stopped;

  const returnRate =
    task.initial_cash > 0 ? (task.total_pnl || 0) / task.initial_cash : 0;
  const isStopped = task.status === "stopped";

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
            {task.strategy_name || task.symbol}
          </span>
          <span className="text-[10px] text-text-muted">
            {task.symbol} / {task.timeframe} / {task.leverage}x
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
      {isStopped && (
        <div className="flex items-center gap-1 self-end">
          <AntButton
            type="text"
            size="small"
            aria-label="重新开始"
            onClick={(e) => {
              e.stopPropagation();
              onRestart();
            }}
          >
            <RotateCcw className="h-3 w-3" style={{ color: "#F59E0B" }} />
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
  const restartMut = useRestartTradingTask();
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
        (t.strategy_name || "").toLowerCase().includes(q) ||
        t.symbol.toLowerCase().includes(q),
    );
  }, [tasks, searchQuery]);

  // Route state for auto-create from Strategies page
  const [pendingCreate, setPendingCreate] = useState<{
    dna: string;
    symbol: string;
    timeframe: string;
    strategyName: string | null;
    initialCash: number;
    leverage: number;
    direction: string;
  } | null>(null);

  useEffect(() => {
    const state = location.state as {
      dna?: unknown;
      symbol?: string;
      timeframe?: string;
      strategyName?: string;
      initialCash?: number;
      leverage?: number;
      direction?: string;
    } | null;

    if (state?.dna) {
      setPendingCreate({
        dna: typeof state.dna === "string" ? state.dna : JSON.stringify(state.dna),
        symbol: state.symbol || "BTCUSDT",
        timeframe: state.timeframe || "4h",
        strategyName: state.strategyName ?? null,
        initialCash: state.initialCash ?? 100_000,
        leverage: state.leverage ?? 1,
        direction: state.direction ?? "long",
      });
      setDialogOpen(true);
      window.history.replaceState({}, "");
    }
  }, []);

  const handleConfirmCreate = useCallback(
    (params: { initialCash: number; leverage: number; direction: string; confidenceSizingEnabled: boolean }) => {
      if (pendingCreate) {
        createTask.mutate({
          dna_json: pendingCreate.dna,
          symbol: pendingCreate.symbol,
          timeframe: pendingCreate.timeframe,
          strategy_name: pendingCreate.strategyName ?? undefined,
          initial_cash: params.initialCash,
          leverage: params.leverage,
          direction: params.direction,
          confidence_sizing_enabled: params.confidenceSizingEnabled,
        });
        setPendingCreate(null);
      }
      setDialogOpen(false);
    },
    [pendingCreate, createTask],
  );

  const openDeleteConfirm = useCallback((taskId: string) => {
    modal.confirm({
      title: "删除任务",
      content: "将永久删除该任务及所有关联数据（交易记录、权益快照），此操作不可撤销。",
      okText: "确认删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => {
        deleteMut.mutate(taskId);
        if (selectedTaskId === taskId) setSelectedTaskId(null);
      },
    });
  }, [modal, deleteMut, selectedTaskId]);

  const openRestartConfirm = useCallback((task: TradingTask) => {
    modal.confirm({
      title: "重新开始模拟交易",
      content: `使用相同策略（${task.strategy_name || task.symbol}）在 ${task.symbol} / ${task.timeframe} 上创建新任务？初始资金: ${formatCurrency(task.initial_cash)}。`,
      okText: "创建任务",
      cancelText: "取消",
      onOk: () => {
        restartMut.mutate(task.task_id);
      },
    });
  }, [modal, restartMut]);

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
          strategyName={pendingCreate?.strategyName ?? null}
          symbol={pendingCreate?.symbol ?? "BTCUSDT"}
          timeframe={pendingCreate?.timeframe ?? "4h"}
          initialCash={pendingCreate?.initialCash ?? 100_000}
          leverage={pendingCreate?.leverage ?? 1}
          direction={pendingCreate?.direction ?? "long"}
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
            <RunnerStatusBadge
              isAlive={runnerStatus?.is_alive ?? false}
              activeTaskId={runnerStatus?.active_task_id ?? null}
            />
          </div>

          <AntButton
            block
            size="small"
            icon={<Plus className="h-3 w-3" />}
            onClick={() => navigate("/strategies")}
          >
            新建任务
          </AntButton>

          {tasks.length > 3 && (
            <Input
              size="small"
              prefix={<Search className="h-3 w-3 text-text-muted" />}
              placeholder="搜索策略名或交易对..."
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
              onRestart={() => openRestartConfirm(task)}
            />
          ))}
        </div>

        {/* Right panel: detail */}
        <div className="flex-1 flex flex-col gap-3 overflow-y-auto min-h-0">
          {activeTask ? (
            <TaskDetailPanel
              task={activeTask}
              wsConnected={wsConnected}
              onRestart={() => openRestartConfirm(activeTask)}
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
        strategyName={pendingCreate?.strategyName ?? null}
        symbol={pendingCreate?.symbol ?? "BTCUSDT"}
        timeframe={pendingCreate?.timeframe ?? "4h"}
        initialCash={pendingCreate?.initialCash ?? 100_000}
        leverage={pendingCreate?.leverage ?? 1}
        direction={pendingCreate?.direction ?? "long"}
        onConfirm={handleConfirmCreate}
      />

      {/* 确认交互单源=AntD modal 体系（原 AlertDialog×2 收敛） */}
      {/* 删除/重启弹层由 openDeleteConfirm/openRestartConfirm 命令式触发 */}
    </PageTransition>
  );
}

// ---------------------------------------------------------------------------
// Task Detail Panel (right side)
// ---------------------------------------------------------------------------

const TRADE_PAGE_SIZE = 5;

function TaskDetailPanel({
  task,
  wsConnected,
  onRestart,
}: {
  task: TradingTask;
  wsConnected: boolean;
  onRestart: () => void;
}) {
  const stopMut = useStopTradingTask();

  const { data: tradeData } = useTradingTrades(task.task_id, 500);
  const { data: equityData } = useTradingEquity(task.task_id);
  const { data: metricsData } = useTradingMetrics(task.task_id);

  const trades = tradeData?.trades ?? [];
  const totalTrades = tradeData?.total ?? 0;
  const equitySnapshots = equityData?.snapshots ?? [];
  const isRunning = task.status === "running";
  const isActive = isRunning || task.status === "pending";
  const isStopped = task.status === "stopped";
  const mutating = stopMut.isPending;

  const [visibleTrades, setVisibleTrades] = useState(TRADE_PAGE_SIZE);

  // Reset visible count when task changes
  useEffect(() => {
    setVisibleTrades(TRADE_PAGE_SIZE);
  }, [task.task_id]);

  const displayedTrades = trades.slice(0, visibleTrades);
  const hasMore = visibleTrades < trades.length;

  // Account overview computations
  const posSide = toPositionSide(task.position_side);
  const hasPosition = posSide !== "flat";
  const upnl = task.unrealized_pnl ?? 0;
  const equity = hasPosition
    ? (task.balance ?? 0) + (task.position_margin ?? 0) + upnl
    : task.balance ?? 0;
  const equityReturn =
    task.initial_cash > 0 ? (equity - task.initial_cash) / task.initial_cash : 0;

  return (
    <div className="flex flex-col gap-3">
      {/* Account Overview Card */}
      <GlassCard className="p-4">
        {/* Header: strategy info + controls */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-semibold text-text-primary">
              {task.strategy_name || task.symbol}
            </h2>
            <span className="text-xs text-text-muted">
              {task.symbol} / {task.timeframe} / {task.leverage}x
              {task.direction && (
                <span className="ml-1">
                  / {DIR_LABEL[task.direction] ?? task.direction}
                </span>
              )}
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
            {isStopped && (
              <AntButton size="small" onClick={onRestart}>
                <RotateCcw className="h-3 w-3" /> 重新开始
              </AntButton>
            )}
          </div>
        </div>

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

      {/* Performance Metrics */}
      <MetricsDashboard metrics={metricsData} initialCash={task.initial_cash} />

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
          trades={trades}
          height={380}
          isActive={isActive}
        />
        {equitySnapshots.length > 0 && (
          <div className="mt-2 pt-2 border-t border-border-default">
            <span className="text-xs text-text-secondary mb-1 block">权益曲线</span>
            <EquityCurve
              data={equitySnapshots}
              initialCash={task.initial_cash}
              height={120}
            />
          </div>
        )}
      </GlassCard>

      {/* Trade History */}
      <div>
        <h3 className="mb-2 text-sm font-medium text-text-secondary">
          交易记录 ({totalTrades})
        </h3>
        {trades.length === 0 ? (
          <div className="flex items-center justify-center h-20 rounded-lg border border-border-default">
            <span className="text-sm text-text-muted">暂无交易记录</span>
          </div>
        ) : (
          <div className="rounded-lg border border-border-default overflow-hidden">
            <Table<PaperTrade>
              rowKey="id"
              columns={TRADE_COLUMNS}
              dataSource={displayedTrades}
              size="small"
              pagination={false}
            />
            {hasMore && (
              <div className="flex justify-center py-2 border-t border-border-default">
                <AntButton
                  type="text"
                  size="small"
                  className="!text-xs !text-text-muted"
                  onClick={() => setVisibleTrades(trades.length)}
                >
                  查看全部 (共{totalTrades}条)
                </AntButton>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TradeRow
// ---------------------------------------------------------------------------

const ACTION_LABEL: Record<string, string> = {
  open: "开仓",
  close: "平仓",
  add: "加仓",
  reduce: "减仓",
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
  reduce_full: "减仓平仓",
};

/** 交易明细列（TradeRow 迁移为声明式 columns——render 逻辑原样移植） */
const TRADE_COLUMNS: TableProps<PaperTrade>["columns"] = [
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
    render: (v: string) => (
      <span className={cn("text-sm", SIDE_COLOR[v] ?? "text-text-primary")}>
        {SIDE_LABEL[v] ?? v}
      </span>
    ),
  },
  {
    title: "操作",
    dataIndex: "action",
    render: (v: string) => <span className="text-sm text-text-primary">{ACTION_LABEL[v] ?? v}</span>,
  },
  {
    title: "价格",
    dataIndex: "price",
    render: (v: number) => <span className="font-num text-sm text-text-primary">{formatCurrency(v)}</span>,
  },
  {
    title: "盈亏",
    dataIndex: "pnl",
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
      <span className="text-xs text-text-muted">{REASON_LABEL[v ?? ""] ?? v ?? "-"}</span>
    ),
  },
];
