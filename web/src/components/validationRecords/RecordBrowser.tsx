/**
 * 验证记录浏览列表域（upgrade-validation-records-list 5.1——L1-L6）。
 * 卡片流四段卡（标识徽标/statement 引文两行截断/区间币种笔数/收益夏普
 * 回撤固定槽——指标取行内嵌 result JSON 直读）+搜索三路由（hyp_/st_
 * 前缀+statement 关键词）+筛选（verdict 四枚带计数联动=verdict_counts+
 * 类型两枚中文标签+泛化 toggle 交集）+排序三键（服务端执行）+懒加载
 * （每批 50+滚动到底+失败重试不丢内容+已加载 N/总数 M+无值落尾披露）
 * +左右分栏（左卡片流独立滚动/右明细常驻）。
 * 分页语义沿端点契约：六维过滤 WHERE 下推——total=过滤基数、页满恒
 * limit（B2 终态；旧「行级过滤不进 total」口径句已退役）。
 */
import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Button as AntButton, Input, Select, Switch } from "antd";
import { RotateCcw, Search } from "lucide-react";
import {
  getValidationRecordsPage,
  VALIDATION_TYPE_OPTIONS,
  type ValidationRecordRow,
  type ValidationSort,
} from "@/services/validationRecords";
import { formatPercent } from "@/lib/utils";
import {
  buildCardModel,
  DEFAULT_LIST_FILTERS,
  parseSearch,
  type ListFilters,
} from "./traceSemantics";
import { RecordDetail } from "./RecordDetail";

export type { ValidationRecordRow } from "@/services/validationRecords";
export type { ListFilters } from "./traceSemantics";

const VERDICT_OPTIONS = [
  { value: undefined, label: "全部", mark: "●" },
  { value: "pass", label: "pass", mark: "✓" },
  { value: "fail", label: "fail", mark: "✗" },
  { value: "inconclusive", label: "证据不足", mark: "◐" },
] as const;

const SORT_OPTIONS: Array<{ value: ValidationSort; label: string }> = [
  { value: "time_desc", label: "时间 ▼新" },
  { value: "total_return_desc", label: "收益" },
  { value: "verdict_quality", label: "结果" },
];

/** V-S 筛选栏（L2/L3/L4——搜索三路由+verdict 四枚带计数+类型两枚+
 *  泛化 toggle+排序三键+重置；全部服务端执行） */
export function FilterBar({
  filters,
  onChange,
  counts,
}: {
  filters: ListFilters;
  onChange: (f: ListFilters) => void;
  counts?: {
    total: number;
    pass: number;
    fail: number;
    inconclusive: number;
  };
}) {
  const [text, setText] = useState("");
  const fire = (patch: Partial<ListFilters>) => onChange({ ...filters, ...patch });
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="vr-filter-bar">
      <Input
        prefix={<Search className="h-3 w-3" />}
        placeholder="假设/策略 ID（hyp_/st_ 前缀）或 statement 关键词"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onPressEnter={() => fire(parseSearch(text))}
        size="small"
        className="!w-72 !text-xs"
        allowClear
        data-testid="vr-search-input"
      />
      <Select
        size="small"
        className="!w-36"
        value={filters.level}
        placeholder="类型"
        allowClear
        onChange={(v) => fire({ level: v })}
        options={VALIDATION_TYPE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        data-testid="vr-type-select"
      />
      <span className="flex items-center gap-1 text-[10px] text-text-muted">
        泛化
        <Switch
          size="small"
          checked={filters.generalization === true}
          onChange={(v) => fire({ generalization: v || undefined })}
          data-testid="vr-generalization-toggle"
        />
      </span>
      <div className="flex items-center gap-1" data-testid="vr-verdict-group">
        {VERDICT_OPTIONS.map((o) => {
          const active = filters.verdict === o.value;
          return (
            <AntButton
              key={o.label}
              size="small"
              type={active ? "primary" : "text"}
              className="!text-[11px]"
              onClick={() => fire({ verdict: o.value })}
              data-testid={`vr-verdict-${o.value ?? "all"}`}
            >
              {o.mark} {o.label}
              {counts ? ` ${o.value === undefined ? counts.total : counts[o.value]}` : ""}
            </AntButton>
          );
        })}
      </div>
      <div className="flex items-center gap-1" data-testid="vr-sort-group">
        {SORT_OPTIONS.map((o) => (
          <AntButton
            key={o.value}
            size="small"
            type={filters.sort === o.value ? "primary" : "text"}
            className="!text-[11px]"
            onClick={() => fire({ sort: o.value })}
            data-testid={`vr-sort-${o.value}`}
          >
            {o.label}
          </AntButton>
        ))}
      </div>
      <AntButton
        size="small"
        icon={<RotateCcw className="h-3 w-3" />}
        onClick={() => {
          setText("");
          onChange({ ...DEFAULT_LIST_FILTERS });
        }}
      >
        重置
      </AntButton>
    </div>
  );
}

const VERDICT_BADGE: Record<string, { text: string; cls: string }> = {
  pass: { text: "✓ 通过", cls: "text-profit" },
  fail: { text: "✗ 未通过", cls: "text-loss" },
  inconclusive: { text: "◐ 证据不足", cls: "text-attention-gold" },
};

/** L1 卡片流四段卡（视觉权威源：原型 03 帧卡片——标识徽标/statement
 *  引文两行截断/区间币种笔数/收益夏普回撤固定槽） */
export function RecordCard({
  row,
  selected,
  onSelect,
}: {
  row: ValidationRecordRow;
  selected: boolean;
  onSelect: (r: ValidationRecordRow) => void;
}) {
  const m = buildCardModel(row);
  const badge = VERDICT_BADGE[row.verdict] ?? { text: row.verdict, cls: "" };
  return (
    <button
      type="button"
      onClick={() => onSelect(row)}
      data-testid={`vr-card-${row.record_id}`}
      className={`w-full rounded-md border border-border-faint bg-bg-base/40 p-2.5 text-left transition-colors hover:border-border-default ${
        selected ? "border-selected bg-accent-gold/5" : ""
      }`}
    >
      {/* 段 1：标识与判定徽标 */}
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-[11px] text-text-secondary">
            {row.record_id}
          </span>
          <span className="shrink-0 rounded border border-border-faint px-1 text-[10px] text-text-muted">
            {m.typeLabel}
          </span>
        </span>
        <span className={`shrink-0 text-[11px] font-medium ${badge.cls}`}>
          {badge.text}
        </span>
      </div>
      {/* 段 2：statement 引文两行截断（无关联显「—」） */}
      <p
        className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-text-secondary"
        title={m.statement ?? undefined}
      >
        {m.statement ? `「${m.statement}」` : "—"}
      </p>
      {/* 段 3：区间 · 币种 · 笔数 */}
      <p className="mt-1 font-num text-[10px] text-text-muted">
        {m.rangeLabel} · {m.symbol ?? "—"} · {m.trades ?? "—"} 笔
      </p>
      {/* 段 4：收益 · 夏普 · 回撤固定槽（result 直读；空显「无指标留痕」） */}
      {m.metrics ? (
        <p className="mt-1 flex items-baseline gap-3 font-num text-xs">
          <span
            className={`font-medium ${
              m.metrics.total_return! >= 0 ? "text-profit" : "text-loss"
            }`}
          >
            {formatPercent(m.metrics.total_return!)}
          </span>
          {m.worstLabel && (
            <span className="text-[9px] text-text-muted">{m.worstLabel}</span>
          )}
          <span className="text-text-secondary">
            夏普 {m.metrics.sharpe_ratio?.toFixed(2) ?? "—"}
          </span>
          <span className="text-text-secondary">
            回撤{" "}
            {m.metrics.max_drawdown != null
              ? `${(m.metrics.max_drawdown * 100).toFixed(1)}%`
              : "—"}
          </span>
        </p>
      ) : (
        <p className="mt-1 text-[10px] text-text-muted">无指标留痕</p>
      )}
    </button>
  );
}

/** L5 懒加载卡片流（useInfiniteQuery——三批拼接无重复无跳号：offset
 *  递增+服务端次序键稳定；失败重试不丢已加载页） */
export function RecordCards({
  filters,
  onSelect,
  selectedId,
  onCounts,
}: {
  filters: ListFilters;
  onSelect: (r: ValidationRecordRow) => void;
  selectedId?: string;
  onCounts?: (c: {
    total: number;
    pass: number;
    fail: number;
    inconclusive: number;
  }) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const q = useInfiniteQuery({
    queryKey: ["validation-records-page", filters],
    queryFn: ({ pageParam }) =>
      getValidationRecordsPage({
        [filters.idType === "q" ? "q" : filters.idType]: filters.idType === "q"
          ? filters.q
          : filters.id,
        level: filters.level,
        verdict: filters.verdict,
        generalization: filters.generalization,
        sort: filters.sort,
        limit: 50,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last) =>
      last.has_more ? last.offset + last.limit : undefined,
  });
  const pages = q.data?.pages ?? [];
  const rows = pages.flatMap((p) => p.records);
  const first = pages[0];
  const total = first?.total ?? 0;

  // 计数联动上抛（verdict 四枚带计数=verdict_counts；与 total 同基数）
  useEffect(() => {
    if (first) {
      onCounts?.({
        total: first.total,
        pass: first.verdict_counts?.pass ?? 0,
        fail: first.verdict_counts?.fail ?? 0,
        inconclusive: first.verdict_counts?.inconclusive ?? 0,
      });
    }
  }, [first, onCounts]);

  // 筛选维度变化回到顶部（翻页后改筛选的滚动残留防御——Q4 同族）
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [filters]);

  // 收窄前解构（isFetchNextPageError 分支里 q 被收窄为 never——判别
  // 联合的类型面现象，运行时无碍；解构绕开收窄）
  const fetchNext = q.fetchNextPage;
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48) {
      if (q.hasNextPage && !q.isFetchingNextPage) void fetchNext();
    }
  };

  // 首页失败=全失败态（无内容可保留）；翻页失败（isError 且已有页）
  // =已加载内容保留+底部重试行——失败重试不丢内容
  if (q.isError && pages.length === 0) {
    return (
      <div className="flex flex-col items-start gap-2 py-6">
        <p className="text-xs text-loss">验证记录源暂不可达</p>
        <AntButton size="small" onClick={() => void q.refetch()}>
          重试
        </AntButton>
      </div>
    );
  }
  if (q.isLoading) return <p className="text-xs text-text-muted">装载中…</p>;

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="flex max-h-[calc(100vh-260px)] flex-col gap-2 overflow-y-auto pr-1"
      data-testid="vr-card-scroll"
    >
      {/* 排序落尾披露（L4——unsortable_count 与 total 同基数） */}
      {filters.sort === "total_return_desc" && (first?.unsortable_count ?? 0) > 0 && (
        <p className="text-[10px] text-attention-gold" data-testid="vr-unsortable-note">
          {first!.unsortable_count} 条无收益值已列末尾
        </p>
      )}
      {rows.length === 0 ? (
        <p className="py-6 text-xs text-text-muted">
          无匹配记录——调整筛选或搜索词后重试
        </p>
      ) : (
        rows.map((r) => (
          <RecordCard
            key={r.record_id}
            row={r}
            selected={selectedId === r.record_id}
            onSelect={onSelect}
          />
        ))
      )}
      <p className="pt-1 text-[10px] text-text-muted" data-testid="vr-load-status">
        已加载 {rows.length} / 总数 {total} 条
        {q.isFetchingNextPage
          ? " · 加载中…"
          : q.hasNextPage
            ? " · 下拉自动加载下一批"
            : " · 已全部加载"}
      </p>
      {q.isFetchNextPageError && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-loss">本批加载失败</span>
          <AntButton
            size="small"
            className="!text-[10px]"
            onClick={() => void fetchNext()}
          >
            重试
          </AntButton>
        </div>
      )}
    </div>
  );
}

/** 浏览骨架宿主（L6 左右分栏：左卡片流独立滚动/右明细常驻） */
export function RecordsBrowser() {
  const [filters, setFilters] = useState<ListFilters>(DEFAULT_LIST_FILTERS);
  const [selected, setSelected] = useState<ValidationRecordRow | undefined>();
  const [counts, setCounts] = useState<
    | { total: number; pass: number; fail: number; inconclusive: number }
    | undefined
  >();
  // 筛选/排序变化清选中（选中行可能已不在结果集）
  const handleFilters = (f: ListFilters) => {
    setFilters(f);
    setSelected(undefined);
  };
  return (
    <div className="space-y-2" data-testid="records-browser">
      <FilterBar filters={filters} onChange={handleFilters} counts={counts} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="rounded-lg border border-border-faint p-2">
          <RecordCards
            filters={filters}
            onSelect={setSelected}
            selectedId={selected?.record_id}
            onCounts={setCounts}
          />
        </div>
        <div className="rounded-lg border border-border-faint p-2">
          <RecordDetail key={selected?.record_id} record={selected} />
        </div>
      </div>
    </div>
  );
}

// （useInvalidateRecords 发起器失效工具随 verify 发起器退役零消费删除——
//  fix-replay-gap-alignment U8 批次收口）
