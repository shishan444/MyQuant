import { useQuery } from "@tanstack/react-query";
import {
  BookOpenCheck, Boxes, FileClock, FlaskConical, Layers,
  LibraryBig, ShieldCheck, Sigma, Workflow,
} from "lucide-react";
import { PageTransition } from "@/components/PageTransition";
import { EmptyState } from "@/components/EmptyState";
import { GlassCard } from "@/components/GlassCard";
import {
  getFormulaPlugins, getModelPlugins, getSignalColumns, getVocabulary,
  type FormulaPluginRow, type ModelPluginRow,
} from "@/services/plugins";
import { cn } from "@/lib/utils";

/**
 * 11 插件与组件库（alignment-batch D6，只读披露页）。
 * 五区块：B1 公式插件审计表 / B2 模型插件三态 / B3 研究词表卡 /
 * B4 四层池静态名单+registry 动态数 / B5 注册协议要点。
 * 无任何增删操作——注册走研究侧（MCP/后端），本页只消费审计端点。
 */

// ---------------------------------------------------------------------------
// 状态徽标（formula: valid|rejected；model: pending|valid|rejected）
// ---------------------------------------------------------------------------

const STATUS_BADGES: Record<string, { label: string; cls: string }> = {
  valid: { label: "有效", cls: "bg-emerald-400/15 text-emerald-400" },
  pending: { label: "训练中", cls: "bg-amber-400/15 text-amber-400" },
  rejected: { label: "已拒绝", cls: "bg-red-400/15 text-red-400" },
};

function StatusBadge({ status }: { status: string }) {
  const badge = STATUS_BADGES[status] ?? { label: status, cls: "bg-white/10 text-text-secondary" };
  return (
    <span className={cn("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap", badge.cls)}>
      {badge.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 区块壳：标题+说明（三态纪律：loading/error/empty 由各区块自理）
// ---------------------------------------------------------------------------

function SectionCard({ icon: Icon, title, subtitle, children, testid }: {
  icon: React.ElementType;
  title: string;
  subtitle: string;
  children: React.ReactNode;
  testid: string;
}) {
  return (
    // testid 落内层 div（GlassCard 不透传 rest props）
    <GlassCard className="p-6" hover={false}>
      <div className="flex flex-col" data-testid={testid}>
        <div className="flex items-start gap-3 mb-4">
          <Icon className="h-5 w-5 text-accent-gold shrink-0 mt-0.5" />
          <div>
            <h2 className="text-base font-medium text-text-primary">{title}</h2>
            <p className="text-xs text-text-muted mt-0.5">{subtitle}</p>
          </div>
        </div>
        {children}
      </div>
    </GlassCard>
  );
}

function SectionError({ onRetry }: { onRetry: () => void }) {
  return (
    <EmptyState
      icon={ShieldCheck}
      title="加载失败"
      description="数据加载失败，请重试"
      actions={[{ label: "重试", onClick: onRetry }]}
    />
  );
}

// ---------------------------------------------------------------------------
// B1 公式插件审计表
// ---------------------------------------------------------------------------

function FormulaPluginsSection() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["plugins", "formula"],
    queryFn: getFormulaPlugins,
  });

  let body: React.ReactNode;
  if (isLoading) {
    body = <div className="h-24 animate-pulse rounded-lg bg-white/[0.04]" data-testid="formula-loading" />;
  } else if (isError) {
    body = <SectionError onRetry={() => refetch()} />;
  } else if (!data || data.items.length === 0) {
    body = <p className="text-sm text-text-muted py-6 text-center">暂无公式插件（注册入口在研究侧）</p>;
  } else {
    body = (
      <div className="overflow-x-auto" data-testid="formula-table">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-default/40 text-text-muted">
              <th className="py-2 pr-3 text-left font-medium">名称</th>
              <th className="py-2 pr-3 text-left font-medium">版本</th>
              <th className="py-2 pr-3 text-left font-medium">状态</th>
              <th className="py-2 pr-3 text-left font-medium">表达式</th>
              <th className="py-2 pr-3 text-left font-medium">输出列</th>
              <th className="py-2 pr-3 text-left font-medium">warmup_bars</th>
              <th className="py-2 text-left font-medium">依赖闭包</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((row) => (
              <FormulaRow key={row.plugin_id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <SectionCard
      icon={Sigma}
      title="公式插件（版本审计）"
      subtitle="受限 DSL 表达式注册留档；warmup_bars 与依赖闭包为查询期现算（不落库）"
      testid="plugins-formula"
    >
      {body}
    </SectionCard>
  );
}

function FormulaRow({ row }: { row: FormulaPluginRow }) {
  const outputs = Array.isArray(row.output_columns) ? row.output_columns : [String(row.output_columns)];
  return (
    <tr className="border-b border-border-default/20 align-top">
      <td className="py-2 pr-3 text-text-primary whitespace-nowrap">
        {row.name}
        {row.status === "rejected" && row.reject_reason && (
          <div className="text-[11px] text-red-400/90 mt-0.5 max-w-48 whitespace-normal">{row.reject_reason}</div>
        )}
      </td>
      <td className="py-2 pr-3 font-mono text-text-secondary">v{row.version}</td>
      <td className="py-2 pr-3"><StatusBadge status={row.status} /></td>
      <td className="py-2 pr-3 font-mono text-text-secondary max-w-72 break-all">{row.expression}</td>
      <td className="py-2 pr-3 font-mono text-text-secondary">
        {outputs.map((c) => <div key={c} className="whitespace-nowrap">{c}</div>)}
      </td>
      {/* null=列不在注册表（无推导依据），与数值 0 区分呈现 */}
      <td className="py-2 pr-3 font-mono text-text-secondary whitespace-nowrap">
        {row.warmup_bars == null ? <span className="text-text-muted">—（未在册）</span> : row.warmup_bars}
      </td>
      <td className="py-2 font-mono text-text-secondary">
        {row.dependency_closure.length === 0
          ? <span className="text-text-muted">—</span>
          : row.dependency_closure.map((c) => <div key={c} className="whitespace-nowrap">{c}</div>)}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// B2 模型插件三态
// ---------------------------------------------------------------------------

function ModelPluginsSection() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["plugins", "model"],
    queryFn: getModelPlugins,
  });

  let body: React.ReactNode;
  if (isLoading) {
    body = <div className="h-24 animate-pulse rounded-lg bg-white/[0.04]" data-testid="model-loading" />;
  } else if (isError) {
    body = <SectionError onRetry={() => refetch()} />;
  } else if (!data || data.items.length === 0) {
    body = <p className="text-sm text-text-muted py-6 text-center">暂无模型插件</p>;
  } else {
    body = (
      <div className="flex flex-col gap-2" data-testid="model-list">
        {data.items.map((row) => (
          <ModelRow key={row.plugin_id} row={row} />
        ))}
      </div>
    );
  }

  return (
    <SectionCard
      icon={FlaskConical}
      title="模型插件（三态）"
      subtitle="参数化模板异步训练：训练中 pending → 有效 valid / 已拒绝 rejected（failed 或超时附原因）"
      testid="plugins-model"
    >
      {body}
    </SectionCard>
  );
}

function ModelRow({ row }: { row: ModelPluginRow }) {
  const oos = typeof row.oos_summary === "object" && row.oos_summary !== null
    ? row.oos_summary
    : null;
  return (
    <div className="rounded-lg border border-border-default/30 bg-white/[0.01] px-3 py-2 flex flex-col gap-1.5">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-text-primary font-medium">{row.name}</span>
        <span className="font-mono text-text-muted">v{row.version}</span>
        <StatusBadge status={row.status} />
        <span className="font-mono text-text-muted ml-auto">{row.signal_column}</span>
      </div>
      {row.status === "rejected" && row.reject_reason && (
        <div className="text-[11px] text-red-400/90" data-testid={`model-reject-reason-${row.plugin_id}`}>
          拒绝原因：{row.reject_reason}
        </div>
      )}
      {oos && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-text-secondary">
          {Object.entries(oos).map(([k, v]) => (
            <span key={k} className="font-mono">{k}: {String(v)}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// B3 词表卡（四族分组；caliber_note 原文披露）
// ---------------------------------------------------------------------------

/** 呈现族序：权威口径四族在前，未知族（后端扩词未映射的兜底键）排后不丢 */
const FAMILY_ORDER = ["时序变换", "时序结构", "逐元素", "数学词汇"];

function VocabularySection() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["plugins", "vocabulary"],
    queryFn: getVocabulary,
  });

  let body: React.ReactNode;
  if (isLoading) {
    body = <div className="h-24 animate-pulse rounded-lg bg-white/[0.04]" data-testid="vocabulary-loading" />;
  } else if (isError) {
    body = <SectionError onRetry={() => refetch()} />;
  } else if (!data) {
    body = <p className="text-sm text-text-muted py-6 text-center">暂无词表数据</p>;
  } else {
    const families = Object.keys(data.families).sort(
      (a, b) => {
        const ia = FAMILY_ORDER.indexOf(a); const ib = FAMILY_ORDER.indexOf(b);
        return (ia === -1 ? FAMILY_ORDER.length : ia) - (ib === -1 ? FAMILY_ORDER.length : ib);
      },
    );
    body = (
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3" data-testid="vocabulary-families">
          {families.map((family) => (
            <div key={family} className="rounded-lg border border-border-default/30 bg-white/[0.01] p-3" data-testid={`vocabulary-family-${family}`}>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-medium text-text-primary">{family}</span>
                <span className="font-mono text-[11px] text-accent-gold bg-accent-gold/10 rounded px-1.5 py-0.5">
                  {data.counts[family] ?? data.families[family].length} 词
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {data.families[family].map((word) => (
                  <code key={word} className="rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[11px] text-text-secondary">
                    {word}
                  </code>
                ))}
              </div>
            </div>
          ))}
        </div>
        {/* 双口径披露：原文呈现，不改写 */}
        <div className="rounded-lg border border-border-default/30 bg-white/[0.01] p-3" data-testid="vocabulary-caliber-note">
          <div className="text-[11px] font-medium text-text-muted mb-1">口径说明（共 {data.total} 词）</div>
          <p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-wrap">{data.caliber_note}</p>
        </div>
      </div>
    );
  }

  return (
    <SectionCard
      icon={LibraryBig}
      title={`研究词表（${data?.total ?? "…"} 词公式 DSL）`}
      subtitle="公式插件表达式的可用词白名单，按族分组；权威源为后端注册表（响应现算非快照）"
      testid="plugins-vocabulary"
    >
      {body}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// B4 四层池（静态名单投影+registry 动态数）
// ---------------------------------------------------------------------------

/**
 * 四层池名单：静态投影自《数学算法组件库-完整需求-2026-10-28.md》
 * （快照权威在需求文档，本页不做端点化、不提供增删操作——运行态只有
 * 「已注册插件列数」从 registry 端点现查）。
 */
const POOLS: Array<{ name: string; note: string; items: string[] }> = [
  {
    name: "默认池",
    note: "9 项，直接可用",
    items: ["ATR 自适应", "EWMA 追踪线", "卡尔曼追踪线", "卡尔曼方向信号", "小波去噪", "series 结构线（限顺耦合）", "方向门控", "FundingPressure", "BB 通道"],
  },
  {
    name: "候选池",
    note: "约 8 项，须配市况条件——未配市况条件不得单独入假设",
    items: ["滚动分位", "Parkinson", "Garman-Klass", "Hurst", "ADX 等"],
  },
  {
    name: "黑名单",
    note: "8 项，写死禁用——命中即整卡排除，不因叠加其他组件解禁",
    items: ["trail 2%", "同轨道 series 线", "长持有策略×任何止损", "卡尔曼做多（裸用）", "两级路由", "动量交叉族", "趋势跟踪做多", "裸深度网络"],
  },
  {
    name: "观察池",
    note: "4 项，挂起——不进入方向枚举，不投入研究预算",
    items: ["GARCH", "HMM", "粒子滤波", "扩展卡尔曼"],
  },
];

function PoolsSection() {
  // 动态数独立查询：signals 失败不影响静态名单呈现（静态名单是文档事实）
  const { data: signals, isLoading, isError, refetch } = useQuery({
    queryKey: ["plugins", "signals"],
    queryFn: getSignalColumns,
  });

  const pluginCount = signals?.filter((s) => s.source !== "builtin").length;

  return (
    <SectionCard
      icon={Layers}
      title="四层池（数学组件）"
      subtitle="名单来源：研究需求文档（静态投影）——本页只读呈现，不做增删操作"
      testid="plugins-pools"
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3" data-testid="pools-grid">
          {POOLS.map((pool) => (
            <div key={pool.name} className="rounded-lg border border-border-default/30 bg-white/[0.01] p-3" data-testid={`pool-${pool.name}`}>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-xs font-medium text-text-primary">{pool.name}</span>
                <span className="font-mono text-[11px] text-accent-gold bg-accent-gold/10 rounded px-1.5 py-0.5">
                  {pool.name === "默认池" ? "9" : pool.name === "候选池" ? "约 8" : pool.name === "黑名单" ? "8" : "4"} 项
                </span>
              </div>
              <p className="text-[11px] text-text-muted mb-2">{pool.note}</p>
              <div className="flex flex-wrap gap-1.5">
                {pool.items.map((item) => (
                  <span key={item} className={cn(
                    "rounded px-1.5 py-0.5 text-[11px]",
                    pool.name === "黑名单"
                      ? "bg-red-400/10 text-red-400/90"
                      : "bg-white/[0.05] text-text-secondary",
                  )}>
                    {item}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
        {/* 动态数：registry 现查（插件条目=非 builtin 条目） */}
        <div className="flex items-center gap-2 text-xs text-text-secondary" data-testid="pools-registered-count">
          <BookOpenCheck className="h-3.5 w-3.5 text-accent-gold" />
          {isLoading && <span className="text-text-muted">已注册插件列：加载中…</span>}
          {isError && (
            <>
              <span className="text-text-muted">已注册插件列：加载失败</span>
              <button type="button" className="text-accent-gold hover:underline" onClick={() => refetch()}>重试</button>
            </>
          )}
          {pluginCount != null && (
            <>
              <span>已注册插件列 <span className="font-mono text-accent-gold">{pluginCount}</span>（来自信号注册表现查，内置指标列除外）</span>
            </>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// B5 协议要点（静态文案：从后端注册/审计行为总结）
// ---------------------------------------------------------------------------

const PROTOCOL_POINTS: Array<{ icon: React.ElementType; title: string; detail: string }> = [
  {
    icon: ShieldCheck,
    title: "静态校验三关",
    detail: "表达式词白名单（注册表现算）、语法结构、插件列在注册表可达——任一不过即拒绝注册（422 结构化错误）。",
  },
  {
    icon: FileClock,
    title: "版本化留档",
    detail: "同名插件升版递增（name+version 唯一），历史版本可查——审计表保留全部版本。",
  },
  {
    icon: BookOpenCheck,
    title: "rejected 可查",
    detail: "校验失败同时留 rejected 记录并附原因，不静默丢失；模型插件训练 failed/超时同样转 rejected+reason。",
  },
  {
    icon: LibraryBig,
    title: "词表白名单",
    detail: "公式 DSL 仅可用研究词表内词汇（见上方词表卡）；扩词须同步后端注册表与族映射（启动期断言 fail-loud）。",
  },
  {
    icon: Workflow,
    title: "审计现算不落库",
    detail: "warmup_bars 与依赖闭包为查询期现算推导值，不落列——避免第二事实源；列不在册时无推导依据如实呈现。",
  },
];

function ProtocolSection() {
  return (
    <SectionCard
      icon={Boxes}
      title="插件注册协议要点"
      subtitle="从后端注册与审计行为总结（只读披露；注册入口在研究侧）"
      testid="plugins-protocol"
    >
      <ol className="flex flex-col gap-2.5">
        {PROTOCOL_POINTS.map(({ icon: Icon, title, detail }, i) => (
          <li key={title} className="flex items-start gap-2.5">
            <span className="shrink-0 mt-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-accent-gold/10 font-mono text-[11px] text-accent-gold">
              {i + 1}
            </span>
            <div>
              <span className="text-xs font-medium text-text-primary">
                <Icon className="mr-1 inline h-3 w-3 align-[-1px] text-accent-gold" />
                {title}
              </span>
              <span className="text-xs text-text-secondary"> —— {detail}</span>
            </div>
          </li>
        ))}
      </ol>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function Plugins() {
  return (
    <PageTransition>
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-lg font-medium text-text-primary">插件与组件库</h1>
          <p className="text-sm text-text-muted mt-1">
            公式/模型插件审计、研究词表与数学组件四层池的只读披露——注册与迭代在研究侧进行
          </p>
        </div>
        <FormulaPluginsSection />
        <ModelPluginsSection />
        <VocabularySection />
        <PoolsSection />
        <ProtocolSection />
      </div>
    </PageTransition>
  );
}
