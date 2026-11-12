import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { AlertTriangle, Database, HeartPulse, Inbox, TrendingUp } from "lucide-react";
import { GlassCard } from "@/components/GlassCard";
import { IntegrityMatrix } from "@/components/data/IntegrityPanels";
import { formatTimestamp } from "@/lib/utils";
import {
  getPendingEvents,
  getValidationRecords,
  type PendingEvent,
} from "@/services/workbench";
import {
  ActiveHypothesesPanel,
  AnalysisTasksPanel,
  MarketSnapshotCard,
  ResearchLogPanel,
  SchedulerStatusLine,
} from "@/components/workbench/ObservationPanels";

/**
 * 01 观察工作台（workbench-observation 前端组）——五区只读观察页：
 * B1 待裁决事件（金色置顶）/B2 AI 循环动态五子区/B3 模拟任务/B4 数据健康。
 * 全页零操作（观察面——动作经导航跳转各域页）；四态覆盖（载入/空/错误/数据）。
 */

const EVENT_META: Record<PendingEvent["type"], { label: string; desc: string; nav: string }> = {
  union_exhausted: { label: "并集耗尽", desc: "验证数据段已全部判过——需评估是否收束该假设", nav: "/lab" },
  qualify_pending: { label: "达标待停", desc: "模拟任务两维已满足——待人工停止出终判报告", nav: "/trading" },
  trigger_deviation: { label: "触发差异", desc: "终判触发一致性未过（差异>10%）——需归因", nav: "/trading" },
};

const VERDICT_COLOR: Record<string, string> = {
  pass: "text-profit", fail: "text-loss", inconclusive: "text-text-muted",
};

function EventRow({ ev }: { ev: PendingEvent }) {
  const navigate = useNavigate();
  const meta = EVENT_META[ev.type];
  const p = ev.payload;
  return (
    <button
      type="button"
      onClick={() => navigate(meta.nav)}
      className="flex w-full items-center gap-3 rounded border border-attention-gold/40 bg-attention-gold/5 px-3 py-2 text-left transition-colors hover:bg-attention-gold/10"
      data-testid={`event-${ev.type}`}
    >
      <span className="text-[10px] font-semibold uppercase tracking-wide text-attention-gold">
        {meta.label}
      </span>
      <span className="font-mono text-xs text-text-secondary">
        {String(p.hypothesis_id ?? p.strategy_id ?? p.task_id ?? "")}
        {p.symbol ? ` · ${String(p.symbol)} ${String(p.timeframe ?? "")}` : ""}
      </span>
      <span className="ml-auto text-xs text-text-muted">{meta.desc} →</span>
    </button>
  );
}

export function Workbench() {
  const eventsQ = useQuery({
    queryKey: ["workbench", "pending-events"],
    queryFn: getPendingEvents,
    refetchInterval: 30_000,
  });
  const recordsQ = useQuery({
    queryKey: ["workbench", "validation-records"],
    queryFn: () => getValidationRecords({ limit: 3 }),
    refetchInterval: 60_000,
  });

  const events = eventsQ.data?.events ?? [];
  const degraded = eventsQ.data?.degraded ?? 0;
  const availability = eventsQ.data?.availability;
  const records = recordsQ.data?.records ?? [];

  return (
    <PageSections
      events={events}
      degraded={degraded}
      truncated={eventsQ.data?.truncated ?? false}
      scannedTasks={eventsQ.data?.scanned_tasks ?? 0}
      scannedRunningTasks={eventsQ.data?.scanned_running_tasks ?? 0}
      availability={availability}
      eventsLoading={eventsQ.isLoading}
      eventsError={eventsQ.isError}
      records={records}
      recordsLoading={recordsQ.isLoading}
      recordsError={recordsQ.isError}
    />
  );
}

function PageSections(props: {
  events: PendingEvent[];
  degraded: number;
  truncated: boolean;
  scannedTasks: number;
  scannedRunningTasks: number;
  // availability 完整形（services/workbench.ts:17 三字段——历史 props 窄化
  // 丢 trigger_deviation，CARD-05 恢复消费）
  availability?: {
    qualify_pending: string | null;
    trigger_deviation: string | null;
    reason: string | null;
  };
  eventsLoading: boolean;
  eventsError: boolean;
  records: import("@/services/workbench").ValidationRecordRow[];
  recordsLoading: boolean;
  recordsError: boolean;
}) {
  return (
    <div className="flex flex-col gap-4">
      {/* B1 待裁决事件区（金色置顶） */}
      <GlassCard className="p-3">
        <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
          <AlertTriangle className="h-3.5 w-3.5 text-attention-gold" />
          待裁决事件
        </h2>
        {props.eventsError ? (
          <p className="text-xs text-loss">事件源暂不可达（稍后自动重试）</p>
        ) : props.eventsLoading ? (
          <p className="text-xs text-text-muted">装载中…</p>
        ) : props.events.length === 0 ? (
          <p className="flex items-center gap-2 text-xs text-text-muted">
            <Inbox className="h-3.5 w-3.5" /> 暂无待裁决事件
          </p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {props.events.map((ev, i) => (
              <EventRow key={`${ev.type}-${i}`} ev={ev} />
            ))}
          </div>
        )}
        {props.degraded > 0 && (
          <p className="mt-2 text-[10px] text-text-muted">
            ⚠ {props.degraded} 个事件源扫描失败已跳过（观察面降级）
          </p>
        )}
        {props.truncated && (
          <p className="mt-1 text-[10px] text-attention-gold">
            ⚠ 任务扫描达上限（终态 {props.scannedTasks} / 运行中 {props.scannedRunningTasks}，
            任一面达 1000 即截断）——超出部分未扫，可能有事件遗漏
          </p>
        )}
        {props.availability?.qualify_pending === "unavailable" && (
          <p className="mt-1 text-[10px] text-text-muted">
            达标事件源未建（{props.availability.reason}）——区分「无事件」与「源未建」
          </p>
        )}
        {props.availability?.trigger_deviation === "unavailable" && (
          <p className="mt-1 text-[10px] text-text-muted">
            触发差异事件源未建——区分「无事件」与「源未建」（终态报告缺 qualification 键，说明详见 reason 拼接说明）
          </p>
        )}
      </GlassCard>

      {/* B2 AI 循环动态区（五子区——fe-workbench-wiring 全量接线） */}
      <GlassCard className="p-3">
        <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
          <TrendingUp className="h-3.5 w-3.5" /> AI 循环动态
          <span className="ml-auto text-[10px] text-text-muted">只读观察——操作归 MCP</span>
        </h2>
        <MarketSnapshotCard />
        <ActiveHypothesesPanel />
        {/* B2-3 最近验证结论（3 条——fe-workbench-wiring limit 8→3） */}
        <div className="mb-2">
          <p className="mb-1 text-[10px] uppercase tracking-wide text-text-muted">最近验证结论</p>
          {props.recordsError ? (
            <p className="text-xs text-loss">验证记录源暂不可达（稍后自动重试）</p>
          ) : props.recordsLoading ? (
            <p className="text-xs text-text-muted">装载中…</p>
          ) : props.records.length === 0 ? (
            <p className="text-xs text-text-muted">暂无验证记录</p>
          ) : (
            <ul className="divide-y divide-border-faint">
              {props.records.slice(0, 3).map((r) => (
                <li key={r.record_id} className="flex items-center gap-2 py-1 text-xs">
                  <span className={`font-mono ${VERDICT_COLOR[r.verdict] ?? ""}`}>
                    ● {r.verdict}
                  </span>
                  <span className="font-mono text-text-secondary">{r.hypothesis_id}</span>
                  <span className="text-text-muted">{r.level}</span>
                  <span className="ml-auto font-mono text-[10px] text-text-muted">
                    {formatTimestamp(r.created_at, "minute")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <AnalysisTasksPanel />
        <ResearchLogPanel />
      </GlassCard>

      {/* C 列模拟任务区（fe-workbench-wiring：调度器行真值+舰队底注）+B4 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <GlassCard className="p-3">
          <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
            <TrendingUp className="h-3.5 w-3.5" /> 模拟任务
          </h2>
          <SchedulerStatusLine />
          <p className="rounded border border-border-faint bg-bg-base/40 px-2 py-1 text-[10px] text-text-muted">
            舰队汇总/任务卡/最近终态端点待建（be-workbench-support D11）——合计权益·今日
            增量·三维达标卡随端点落地接线
          </p>
        </GlassCard>
        <GlassCard className="p-3">
          <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
            <HeartPulse className="h-3.5 w-3.5" /> 数据与系统健康
          </h2>
          {/* 月矩阵缩略消费（fe-workbench-wiring）：复用 09 页
              IntegrityMatrix 组件（固定 BTCUSDT+5 月只读——01 观察面零
              操作，详情/切档归 09 页导航）；api/mcp 进程状态为原型文案
              照排+D1 底注（be-workbench-support 归档后接真值） */}
          <div className="flex items-center gap-3 text-xs">
            <Database className="h-3.5 w-3.5 text-text-muted" />
            <span className="text-text-muted">
              api :8000 在线 · mcp :8800 在线
            </span>
          </div>
          <p className="mt-1 rounded border border-border-faint bg-bg-base/40 px-2 py-1 text-[10px] text-text-muted">
            进程状态端点待建（be-workbench-support D1）——当前为原型文案照排
          </p>
          <div className="mt-2">
            <IntegrityMatrix symbol="BTCUSDT" monthsBack={5} />
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
