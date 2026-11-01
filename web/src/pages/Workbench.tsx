import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { AlertTriangle, Bot, Database, HeartPulse, Inbox, TrendingUp } from "lucide-react";
import { GlassCard } from "@/components/GlassCard";
import {
  getPendingEvents,
  getValidationRecords,
  type PendingEvent,
} from "@/services/workbench";

/**
 * 01 观察工作台（workbench-observation 前端组）——五区只读观察页：
 * B1 待裁决事件（金色置顶）/B2 AI 循环动态五子区/B3 纸盘任务/B4 数据健康。
 * 全页零操作（观察面——动作经导航跳转各域页）；四态覆盖（载入/空/错误/数据）。
 */

const EVENT_META: Record<PendingEvent["type"], { label: string; desc: string; nav: string }> = {
  union_exhausted: { label: "并集耗尽", desc: "验证数据段已全部判过——需评估是否收束该假设", nav: "/lab" },
  qualify_pending: { label: "达标待停", desc: "纸盘任务两维已满足——待人工停止出终判报告", nav: "/trading" },
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
    queryFn: () => getValidationRecords({ limit: 8 }),
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

      {/* B2 AI 循环动态区（五子区概要） */}
      <GlassCard className="p-3">
        <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
          <Bot className="h-3.5 w-3.5" /> AI 循环动态
          <span className="ml-auto text-[10px] text-text-muted">只读观察——操作归 MCP</span>
        </h2>
        {/* B2-3 最近验证结论（组A 端点消费——概要默认） */}
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
              {props.records.slice(0, 5).map((r) => (
                <li key={r.record_id} className="flex items-center gap-2 py-1 text-xs">
                  <span className={`font-mono ${VERDICT_COLOR[r.verdict] ?? ""}`}>
                    ● {r.verdict}
                  </span>
                  <span className="font-mono text-text-secondary">{r.hypothesis_id}</span>
                  <span className="text-text-muted">{r.level}</span>
                  <span className="ml-auto font-mono text-[10px] text-text-muted">
                    {r.created_at.slice(0, 16).replace("T", " ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <p className="text-[10px] text-text-muted">
          行情快照/在途假设/分析任务/研究日志四子区随各域端点消费扩展（概要默认·展开详情）
        </p>
      </GlassCard>

      {/* B3 纸盘任务区 + B4 数据健康（占位概要——C6 后矩阵真数据） */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <GlassCard className="p-3">
          <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
            <TrendingUp className="h-3.5 w-3.5" /> 纸盘任务
          </h2>
          <p className="text-xs text-text-muted">
            运行中任务卡（三维点：收益转正/笔数/触发一致性）与舰队权益行——消费
            /api/paper-trading/tasks 列表面（任务卡渲染随 C5 消费面资产扩展）
          </p>
        </GlassCard>
        <GlassCard className="p-3">
          <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-text-secondary">
            <HeartPulse className="h-3.5 w-3.5" /> 数据与系统健康
          </h2>
          <div className="flex items-center gap-3 text-xs">
            <Database className="h-3.5 w-3.5 text-text-muted" />
            <span className="text-text-muted">月×周期矩阵缩略与 api/mcp 进程——随 C6 数据管理真数据</span>
          </div>
          <div className="mt-2 flex gap-2">
            {/* B4 徽章已删（workbench-observation CARD-04：api 恒 ok/mcp 恒
                pending 系硬编码假状态——web-ui-truthfulness 不画假数据，
                随 C6 数据管理真数据再立真状态面） */}
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
