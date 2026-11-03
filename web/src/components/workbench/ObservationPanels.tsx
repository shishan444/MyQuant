/**
 * 观察台 B 列五子区+C 列调度器行组件族（fe-workbench-wiring）：
 * 页面级私有组件（契约三——第二消费者出现才升公共层）。
 * W0 形制统一：isError→区块错误行+TanStack retry；载入「装载中…」；空态可判别。
 * 数值列 font-num；底注形制=端点未落地时「<区名>端点待建」可判别占位。
 */
import { useQuery } from "@tanstack/react-query";
import { Activity, Bot, ClipboardList, ScrollText, Sigma } from "lucide-react";
import {
  getActiveHypotheses,
  getMarketSnapshot,
  getResearchLog,
} from "@/services/workbenchObservation";
import { getTradingRunnerStatus } from "@/services/trading";

function ErrLine({ name }: { name: string }) {
  return <p className="text-xs text-loss">{name}暂不可达（稍后自动重试）</p>;
}
function LoadingLine() {
  return <p className="text-xs text-text-muted">装载中…</p>;
}
function Footnote({ text }: { text: string }) {
  return (
    <p className="rounded border border-border-faint bg-bg-base/40 px-2 py-1 text-[10px] text-text-muted">
      {text}
    </p>
  );
}
function SubTitle({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-1 text-[10px] uppercase tracking-wide text-text-muted">
      {children}
    </p>
  );
}

/** W-B2a 五维行情卡（4h 主档——缺省四档含 1d 因 warmup 不足恒 422，实测在案） */
export function MarketSnapshotCard() {
  const q = useQuery({
    queryKey: ["workbench", "market-snapshot"],
    queryFn: () => getMarketSnapshot(),
    refetchInterval: 60_000,
  });
  const tf = q.data?.timeframes?.["4h"];
  return (
    <div className="mb-2">
      <SubTitle>五维行情 · 4h</SubTitle>
      {q.isError ? (
        <ErrLine name="行情快照" />
      ) : q.isLoading ? (
        <LoadingLine />
      ) : !tf ? (
        <p className="text-xs text-text-muted">无读数</p>
      ) : (
        <div className="grid grid-cols-5 gap-1 text-center">
          {[
            { k: "趋势", v: tf.trend?.state ?? "—", s: tf.trend?.adx },
            { k: "动量", v: tf.momentum ? `RSI ${tf.momentum.rsi ?? "—"}` : "—", s: tf.momentum?.pct },
            {
              k: "波动",
              v: tf.volatility ? `ATR ${tf.volatility.atr_pct ?? "—"}%` : "—",
              s: tf.volatility?.atr_pct_pct,
            },
            { k: "量能", v: tf.volume ? `RVOL ${tf.volume.rvol ?? "—"}` : "—", s: tf.volume?.pct },
            {
              k: "衍生品",
              v: tf.derivatives?.funding ? `费率z ${tf.derivatives.funding.funding_z ?? "—"}` : "—",
              s: tf.derivatives?.oi?.pct,
            },
          ].map((d) => (
            <div key={d.k} className="rounded border border-border-faint px-1 py-1">
              <p className="text-[9px] text-text-muted">{d.k}</p>
              <p className="font-num text-[11px] text-text-secondary">{d.v}</p>
              {d.s !== undefined && (
                <p className="font-num text-[9px] text-text-muted">
                  分位 {Math.round((d.s ?? 0) * 100)}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
      {tf?.as_of && (
        <p className="mt-1 font-num text-[9px] text-text-muted">
          as_of {tf.as_of.slice(0, 16).replace("T", " ")}
          {tf.window_bars ? ` · 窗 ${tf.window_bars} bar` : ""}
        </p>
      )}
    </div>
  );
}

/** W-B2b 在途假设卡组（余量列 D9 前底注） */
export function ActiveHypothesesPanel() {
  const q = useQuery({
    queryKey: ["workbench", "active-hypotheses"],
    queryFn: getActiveHypotheses,
    refetchInterval: 60_000,
  });
  return (
    <div className="mb-2">
      <SubTitle>在途假设</SubTitle>
      {q.isError ? (
        <ErrLine name="假设列表" />
      ) : q.isLoading ? (
        <LoadingLine />
      ) : (q.data?.items?.length ?? 0) === 0 ? (
        <p className="text-xs text-text-muted">暂无在途假设</p>
      ) : (
        <ul className="divide-y divide-border-faint">
          {q.data!.items.slice(0, 5).map((h) => (
            <li key={h.hypothesis_id} className="flex items-center gap-2 py-1 text-xs">
              <span className="font-mono text-text-secondary">{h.hypothesis_id}</span>
              <span className="truncate text-text-muted">
                {h.data_ranges?.[0]
                  ? `${h.data_ranges[0].symbol} · ${h.data_ranges[0].timeframe}`
                  : h.statement?.slice(0, 24) ?? ""}
              </span>
              <span className="ml-auto text-[10px] text-text-muted">余量列待聚合端点</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** W-B2d 分析任务区（A2 定案 12 模板固定行——D8 前底注） */
export function AnalysisTasksPanel() {
  return (
    <div className="mb-2">
      <SubTitle>分析任务 · 12 模板</SubTitle>
      <Footnote text="分析任务端点待建（be-workbench-support D8）——12 模板固定行×各自最近任务态随端点落地接线" />
    </div>
  );
}

/** W-B2e 研究日志流（30 条 desc） */
export function ResearchLogPanel() {
  const q = useQuery({
    queryKey: ["workbench", "research-log"],
    queryFn: () => getResearchLog(30),
    refetchInterval: 60_000,
  });
  return (
    <div className="mb-2">
      <SubTitle>研究日志 · 最近 30 条</SubTitle>
      {q.isError ? (
        <ErrLine name="研究日志" />
      ) : q.isLoading ? (
        <LoadingLine />
      ) : (q.data?.items?.length ?? 0) === 0 ? (
        <p className="text-xs text-text-muted">暂无日志</p>
      ) : (
        <ul className="max-h-32 divide-y divide-border-faint overflow-y-auto">
          {q.data!.items.slice(0, 8).map((e) => (
            <li key={e.log_id} className="flex items-center gap-2 py-0.5 text-[11px]">
              <span className="font-mono text-text-muted">
                {e.created_at.slice(5, 16).replace("T", " ")}
              </span>
              <span className="truncate text-text-secondary">
                {e.summary ?? `${e.entry_type}${e.ref_id ? ` ${e.ref_id}` : ""}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** W-C1 调度器状态行（max=None=未装配态≠0/0——契约要害） */
export function SchedulerStatusLine() {
  const q = useQuery({
    queryKey: ["workbench", "runner-status"],
    queryFn: getTradingRunnerStatus,
    refetchInterval: 30_000,
  });
  return (
    <div className="mb-2">
      <SubTitle>调度器</SubTitle>
      {q.isError ? (
        <ErrLine name="调度器状态" />
      ) : q.isLoading ? (
        <LoadingLine />
      ) : q.data?.max_running_tasks == null ? (
        <p className="text-xs text-text-muted">调度器未启动</p>
      ) : (
        <p className="font-num text-xs text-text-secondary">
          {q.data.running_count}/{q.data.max_running_tasks} · 队列 {q.data.pending_depth}
        </p>
      )}
    </div>
  );
}

/** B2 区聚合容器（五子区） */
export function AILoopPanel({ children }: { children?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-wide text-text-muted">
      <Bot className="h-3 w-3" /> AI 循环动态
      {children}
    </div>
  );
}
export const PanelIcons = { Sigma, ClipboardList, ScrollText, Activity };
