/**
 * 08 实盘交易·目标态预览页（live-trading-preview，demo-only）。
 *
 * 边界：实盘链未建设（live_trading 域为壳 CRUD）——本页为 PRD 第二批
 * 目标态预览：B1/B7 消费 demo 域任务真实数据（壳端点）；B4/B6 后端
 * 不存在，呈现预览样例并如实标注。术语 demo（非 testnet，编号索引
 * v2 修订⑦）；强平价显「—」（demo 域无强平链）。
 * 消费方：App 路由 /live。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, BadgeCheck, Ban, Boxes, FileClock, FlaskConical,
  ListTree, Lock, ScrollText, ShieldCheck, Siren,
} from "lucide-react";
import { Button as AntButton, Modal } from "antd";
import { toast } from "sonner";
import { GlassCard } from "@/components/GlassCard";
import { listLiveTasks, type LiveTradingTask } from "@/services/liveTrading";

/** 风控四条（risk_guard.py:37-40 工程现状权威源，非 PRD 契约值）——B5 一票否决行 */
const RISK_RULES: { label: string; value: string; note?: string }[] = [
  { label: "单笔名义上限", value: "1000U" },
  { label: "最小下单间隔", value: "5s" },
  { label: "日亏损熔断", value: "500U", note: "触及停止开新仓" },
  { label: "保证金占用上限", value: "50%", note: "占权益" },
];

/** 安全三件（REQ-2-14 目标态——总回撤熔断/篮子合计敞口/学费预算；工程现状未建，mainnet 裁决门前置） */
const SAFETY_ITEMS = ["总回撤≥20% 全停", "篮子合计敞口上限", "学费预算·累计净亏损上限"];

/** M1 五要素门槛清单（①工程现状——live_trading.py:38/:48/:60 三层校验实锚；⑤目标态 REQ-2-14） */
type GateItem = { text: string; tag?: "现状" | "目标态" };
const GATE_CHECKLIST: GateItem[] = [
  { text: "创建三层校验：DNA 形式校验 400／值域+插件源 422／交易兼容性 422", tag: "现状" },
  { text: "候选资格：纸盘前向验证达标（qualification 凭证）" },
  { text: "人工裁决门：最终放行须人工确认" },
  { text: "起始资金 200U" },
  { text: `安全三件：${SAFETY_ITEMS.join("／")}`, tag: "目标态" },
];

/** B4 外部干预待确认（目标态预览样例——后端事件流不存在） */
const INTERVENTION_SAMPLE = {
  detected: "连续 3 根 bar 未收到心跳（观测层检测）",
  note: "目标态预览样例——demo 域无外部干预事件流，确认操作仅本地提示",
};

/** B6r 决策快照表样例（4 行，含决策因果依据列+观测层披露） */
const DECISION_ROWS = [
  { time: "12:00:00", action: "开多 0.01 BTC", basis: "signal: ema 快线上穿慢线（bar 收合确认）", observed: "bar 收合 + 求值器触发" },
  { time: "13:30:00", action: "挂保护线 62,140", basis: "risk: trail 档止损线上移（保护线规则）", observed: "仓位层生效" },
  { time: "15:00:00", action: "跳过一次信号", basis: "guard: 下单间隔 <5s 风控否决", observed: "规则层否决记录" },
  { time: "16:00:00", action: "人工停止", basis: "manual: 人工裁决门终止指令", observed: "全冻结生效" },
];

const STATUS_META: Record<string, { label: string; cls: string }> = {
  running: { label: "进行中", cls: "text-profit" },
  pending: { label: "暂停待确认", cls: "text-warning" },
  stopped: { label: "规则停机", cls: "text-text-muted" },
  error: { label: "出错", cls: "text-loss" },
};

function SectionCard({ icon: Icon, title, subtitle, children, testid }: {
  icon: React.ElementType;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  testid: string;
}) {
  return (
    <GlassCard className="p-4">
      <div data-testid={testid}>
        <div className="flex items-baseline gap-2 mb-3">
          <Icon className="h-4 w-4 text-text-muted self-center" />
          <h3 className="text-sm font-medium text-text-primary">{title}</h3>
          {subtitle && <span className="text-[10px] text-text-muted">{subtitle}</span>}
        </div>
        {children}
      </div>
    </GlassCard>
  );
}

function TaskRow({ task, active, onSelect }: {
  task: LiveTradingTask;
  active: boolean;
  onSelect: () => void;
}) {
  const meta = STATUS_META[task.status] ?? { label: task.status, cls: "text-text-muted" };
  return (
    <button
      onClick={onSelect}
      data-testid={`live-task-${task.task_id}`}
      className={`w-full text-left px-3 py-2 rounded border text-xs flex items-center gap-3 ${
        active ? "border-primary/50 bg-primary/5" : "border-border-faint hover:bg-white/5"
      }`}
    >
      <span className="font-mono text-text-secondary">{task.symbol}/{task.timeframe}</span>
      <span className={meta.cls}>{meta.label}</span>
      <span className="ml-auto text-text-muted font-mono">{task.created_at.slice(0, 10)}</span>
    </button>
  );
}

export function LiveTrading() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [m1Open, setM1Open] = useState(false);

  const q = useQuery({
    queryKey: ["live-trading", "tasks"],
    queryFn: () => listLiveTasks(),
    refetchInterval: 60_000,
  });

  const tasks = q.data ?? [];
  const selected = tasks.find((t) => t.task_id === selectedId) ?? tasks[0] ?? null;

  return (
    <div className="p-4 md:p-6 space-y-4" data-testid="live-trading-page">
      {/* 页级 demo 域标注横幅 */}
      <div
        className="rounded border border-warning/40 bg-warning/10 px-4 py-2.5 flex items-start gap-2"
        data-testid="live-demo-banner"
      >
        <FlaskConical className="h-4 w-4 text-warning mt-0.5 shrink-0" />
        <p className="text-xs text-text-secondary leading-5">
          <span className="text-warning font-medium">demo 域预览：</span>
          实盘交易链未建设（当前工程 live_trading 域为壳 CRUD）——本页为 PRD 第二批
          （REQ-2-01~13）目标态预览锚，非实现承诺，不是实盘交易入口。
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 左列：B1 任务列表 */}
        <div className="space-y-4">
          <SectionCard icon={ListTree} title="任务列表" subtitle="demo 域任务（壳端点）" testid="live-b1">
            {q.isLoading && <p className="text-xs text-text-muted">装载中…</p>}
            {q.isError && <p className="text-xs text-loss">demo 域任务端点不可达</p>}
            {!q.isLoading && !q.isError && tasks.length === 0 && (
              <p className="text-xs text-text-muted">无 demo 域任务（空态如实——不造样例任务）</p>
            )}
            <div className="space-y-1.5">
              {tasks.map((t) => (
                <TaskRow key={t.task_id} task={t} active={selected?.task_id === t.task_id}
                  onSelect={() => setSelectedId(t.task_id)} />
              ))}
            </div>
            <AntButton size="small" className="mt-3" onClick={() => setM1Open(true)}
              data-testid="live-m1-trigger">
              创建实盘任务（预览）
            </AntButton>
          </SectionCard>

          {/* B7 demo 域任务参数卡 */}
          <SectionCard icon={FileClock} title="任务参数（demo 域）" testid="live-b7">
            {selected ? (
              <dl className="text-xs space-y-1.5">
                <div className="flex justify-between"><dt className="text-text-muted">交易对/周期</dt><dd className="font-mono">{selected.symbol}/{selected.timeframe}</dd></div>
                <div className="flex justify-between"><dt className="text-text-muted">方向</dt><dd>{selected.direction}</dd></div>
                <div className="flex justify-between"><dt className="text-text-muted">凭证状态</dt><dd className="text-text-muted">未接入（demo 域）</dd></div>
                <div className="flex justify-between"><dt className="text-text-muted">风控四条</dt><dd className="font-mono">1000U/5s/500U/50%</dd></div>
                <div className="flex justify-between"><dt className="text-text-muted">对账节拍</dt><dd className="text-text-muted">未接入（demo 域）</dd></div>
              </dl>
            ) : (
              <p className="text-xs text-text-muted">未选中任务</p>
            )}
          </SectionCard>
        </div>

        {/* 中列：B2 头卡 + B3 仓位条 */}
        <div className="space-y-4">
          <SectionCard icon={Activity} title="任务头卡" testid="live-b2">
            {selected ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center gap-1 rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-black" data-testid="live-badge">
                    <Activity className="h-3 w-3" /> live
                  </span>
                  <span className="text-xs text-text-secondary font-mono">{selected.task_id.slice(0, 10)}</span>
                  <Lock className="h-3.5 w-3.5 text-text-muted" aria-label="全冻结锁标" />
                </div>
                <div className="flex items-center gap-2">
                  <AntButton
                    size="small" danger
                    onClick={() => toast.info("demo 域预览——人工停止不发送指令（实盘链未建设）")}
                    data-testid="live-stop-btn"
                  >
                    人工停止（预览）
                  </AntButton>
                  <span className="text-[10px] text-text-muted">冻结语义：停止后全链只读</span>
                </div>
              </div>
            ) : (
              <p className="text-xs text-text-muted">未选中任务</p>
            )}
          </SectionCard>

          {/* B3 仓位条（与 07-B3 同构形制；强平价「—」） */}
          <SectionCard icon={Boxes} title="实盘仓位" subtitle="与纸盘仓位条同构" testid="live-b3">
            {selected ? (
              <div className="text-xs space-y-1.5">
                <div className="flex justify-between"><span className="text-text-muted">方向/数量</span><dd className="font-mono">{selected.position_side || "—"} / {selected.position_qty?.toFixed(6) ?? "0"}</dd></div>
                <div className="flex justify-between"><span className="text-text-muted">开仓价</span><dd className="font-mono">{selected.position_entry || "—"}</dd></div>
                <div className="flex justify-between"><span className="text-text-muted">权益/未实现</span><dd className="font-mono">{selected.equity?.toFixed(2)} / {selected.unrealized_pnl?.toFixed(4)}</dd></div>
                <div className="flex justify-between" title="demo 域无强平链——强平价不可计算">
                  <span className="text-text-muted">强平价</span>
                  <dd className="font-mono text-text-muted" data-testid="live-liquidation-dash">—</dd>
                </div>
              </div>
            ) : (
              <p className="text-xs text-text-muted">未选中任务</p>
            )}
          </SectionCard>
        </div>

        {/* 右列：B4 外部干预 + B6r 决策快照表 */}
        <div className="space-y-4">
          {/* B4 外部干预待确认（金框——目标态预览样例） */}
          <div className="rounded-lg border-2 border-amber-400/60 bg-amber-400/5 p-4" data-testid="live-b4">
            <div className="flex items-center gap-2 mb-2">
              <Siren className="h-4 w-4 text-amber-400" />
              <h3 className="text-sm font-medium text-text-primary">外部干预待确认</h3>
              <span className="text-[10px] text-amber-400">目标态预览样例</span>
            </div>
            <p className="text-xs text-text-secondary mb-3">{INTERVENTION_SAMPLE.detected}</p>
            <div className="flex gap-2">
              <AntButton size="small" onClick={() => toast.info("demo 域预览——确认操作不发送指令")}>确认继续</AntButton>
              <AntButton size="small" danger onClick={() => toast.info("demo 域预览——确认操作不发送指令")}>确认终止</AntButton>
            </div>
            <p className="text-[10px] text-text-muted mt-2">{INTERVENTION_SAMPLE.note}</p>
          </div>

          {/* B6r 决策快照表 */}
          <SectionCard icon={ScrollText} title="决策快照" subtitle="只读回放·目标态预览样例" testid="live-b6">
            <table className="w-full text-[11px]" data-testid="live-b6-table">
              <thead>
                <tr className="text-text-muted text-left">
                  <th className="py-1 font-normal">时间</th>
                  <th className="py-1 font-normal">动作</th>
                  <th className="py-1 font-normal">依据</th>
                </tr>
              </thead>
              <tbody>
                {DECISION_ROWS.map((r, i) => (
                  <tr key={i} className="border-t border-border-faint align-top">
                    <td className="py-1 pr-1 font-mono text-text-muted">{r.time}</td>
                    <td className="py-1 pr-1 text-text-secondary">{r.action}</td>
                    <td className="py-1 text-text-muted" title={`观测层：${r.observed}`}>{r.basis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </SectionCard>
        </div>
      </div>

      {/* B5 规则层与安全底线 */}
      <SectionCard icon={ShieldCheck} title="规则层与安全底线" subtitle="一票否决行（风控四条真实值）" testid="live-b5">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <span className="text-[10px] text-text-muted">【工程现状】</span>
          {RISK_RULES.map((r) => (
            <span
              key={r.label}
              className="inline-flex items-center gap-1.5 rounded border border-border-faint px-2.5 py-1 text-xs"
              data-testid={`live-risk-${r.value}`}
            >
              <Ban className="h-3 w-3 text-loss" />
              <span className="text-text-secondary">{r.label}</span>
              <span className="font-mono font-semibold text-text-primary">{r.value}</span>
              {r.note && <span className="text-[10px] text-text-muted">{r.note}</span>}
            </span>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[11px] text-text-muted">
          <span className="text-[10px] text-amber-400">【目标态】</span>
          {SAFETY_ITEMS.map((s) => (
            <span key={s} className="inline-flex items-center gap-1">
              <BadgeCheck className="h-3.5 w-3.5" /> {s}
            </span>
          ))}
        </div>
      </SectionCard>

      {/* M1 创建对话框（呈现层——demo 域标注） */}
      <Modal
        open={m1Open}
        onCancel={() => setM1Open(false)}
        footer={null}
        title="创建实盘任务（目标态预览）"
        data-testid="live-m1-modal"
      >
        <p className="text-xs text-text-muted mb-3">
          五要素门槛清单——①创建三层校验为工程现状，②~⑤为目标态样貌呈现。
        </p>
        <ul className="space-y-2 mb-4">
          {GATE_CHECKLIST.map((g, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-text-secondary" data-testid={`live-m1-gate-${i}`}>
              <span className="mt-0.5 h-3.5 w-3.5 rounded-full border border-border-faint shrink-0" aria-hidden />
              {g.tag && (
                <span className={`text-[10px] ${g.tag === "目标态" ? "text-amber-400" : "text-text-muted"}`}>【{g.tag}】</span>
              )}
              {g.text}
            </li>
          ))}
        </ul>
        <div className="text-xs text-text-muted mb-3">
          起始资金 200U；最终放行须人工裁决门确认。
        </div>
        <AntButton
          block
          onClick={() => {
            setM1Open(false);
            toast.info("demo 域预览——创建动作不发送请求（实盘链未建设）");
          }}
          data-testid="live-m1-submit"
        >
          提交创建（demo 域预览）
        </AntButton>
      </Modal>
    </div>
  );
}
