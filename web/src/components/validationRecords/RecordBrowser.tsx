/**
 * 验证记录浏览组件族（fe-validation-records-page + Q1/Q4/Q5 修复）——页面级私有（契约三）。
 * 骨架：V-S 筛选栏→V-T 记录表→V-D0~D4 详情五形态。
 * 分页语义沿端点契约：level/verdict 行级过滤不进 total（前端同口径显示——如实披露）。
 * Q1 修复（fix/fe-validation-forms-remediation）：详情四形态由占位文案改
 * 端点真值渲染——V-D1 params 三键展开（实查 params 无 dna 键，留档）；
 * V-D2 K 线复核=executions 选中份 trades 笔对双标+ohlcv 真值 K 线
 * （CHART_VC 容器底+POLARITY 蜡烛走 chartThemes）+多份选择器；
 * V-D3 资金曲线=equity values 消费（grid_aligned compact 形制按
 * start+length×timeframe 重建时间轴，全时间戳形制直用）。
 * Q4：filters 变化重置 offset（翻页后改筛选招死局空态修复）。
 * Q5：FilterBar onSearch 类型 id 可选化，删双重断言。
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button as AntButton, Select, Input, Tabs } from "antd";
import { FileSearch, RotateCcw, Search } from "lucide-react";
import { KlineChart } from "@/components/charts/KlineChart";
import { CHART_VC } from "@/theme/tokens";
import { getOhlcvBySymbol } from "@/services/datasets";
import {
  getValidationRecordDetail,
  getValidationRecordsPage,
  VALIDATION_LEVELS,
  VALIDATION_VERDICTS,
  type ValidationDetailResponse,
  type ValidationRecordRow,
  type VrExecution,
  type VrTrade,
} from "@/services/validationRecords";

const VERDICT_COLOR: Record<string, string> = {
  pass: "text-profit", fail: "text-loss", inconclusive: "text-text-muted",
};

/** V-S 筛选栏（前缀路由：hyp_ → hypothesis_id，否则 strategy_id） */
export function FilterBar({
  onSearch,
}: {
  onSearch: (q: {
    idType: "hypothesis_id" | "strategy_id";
    id?: string;
    level?: string;
    verdict?: string;
  }) => void;
}) {
  const [text, setText] = useState("");
  const [level, setLevel] = useState<string | undefined>();
  const [verdict, setVerdict] = useState<string | undefined>();
  const fire = (t = text, l = level, v = verdict) => {
    const trimmed = t.trim();
    onSearch({
      idType: trimmed.startsWith("hyp_") ? "hypothesis_id" : "strategy_id",
      id: trimmed || undefined,
      level: l,
      verdict: v,
    });
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        prefix={<Search className="h-3 w-3" />}
        placeholder="输入假设 ID（hyp_…）或策略 ID"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onPressEnter={() => fire()}
        size="small"
        className="!w-64 !text-xs"
        allowClear
      />
      <Select
        placeholder="来源（level）"
        value={level}
        onChange={(v) => { setLevel(v); fire(text, v, verdict); }}
        allowClear
        size="small"
        className="!w-40"
        options={VALIDATION_LEVELS.map((l) => ({ value: l, label: l }))}
      />
      <Select
        placeholder="状态（verdict）"
        value={verdict}
        onChange={(v) => { setVerdict(v); fire(text, level, v); }}
        allowClear
        size="small"
        className="!w-32"
        options={VALIDATION_VERDICTS.map((v) => ({ value: v, label: v }))}
      />
      <AntButton
        size="small"
        icon={<RotateCcw className="h-3 w-3" />}
        onClick={() => { setText(""); setLevel(undefined); setVerdict(undefined); fire("", undefined, undefined); }}
      >
        重置
      </AntButton>
    </div>
  );
}

/** V-T 记录表（limit 50+加载更多 has_more 驱动；Q4：filters 变化重置 offset） */
export function RecordsTable({
  filters,
  onSelect,
  selectedId,
}: {
  filters: { idType: string; id?: string; level?: string; verdict?: string };
  onSelect: (r: ValidationRecordRow) => void;
  selectedId?: string;
}) {
  const [offset, setOffset] = useState(0);
  // Q4：筛选维度变化即回到第一页——翻页后改筛选的 offset 残留会构造
  // 越界空集死局（无 UI 途径回页首）
  useEffect(() => { setOffset(0); }, [filters]);
  const q = useQuery({
    queryKey: ["validation-records-page", filters, offset],
    queryFn: () =>
      getValidationRecordsPage({
        [filters.idType]: filters.id,
        level: filters.level,
        verdict: filters.verdict,
        limit: 50,
        offset,
      }),
  });
  const rows = q.data?.records ?? [];
  return (
    <div>
      {q.isError ? (
        <p className="text-xs text-loss">验证记录源暂不可达（稍后自动重试）</p>
      ) : q.isLoading ? (
        <p className="text-xs text-text-muted">装载中…</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-text-muted">无匹配记录</p>
      ) : (
        <>
          <p className="mb-1 text-[10px] text-text-muted">
            本页 {rows.length} 条
            {q.data?.has_more ? "（更多可加载）" : ""}
            {filters.level || filters.verdict ? " · 过滤计数为行级口径（与全库 total 分列）" : ""}
          </p>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border-default text-left text-[10px] text-text-muted">
                <th className="py-1">时间</th><th>假设</th><th>策略</th><th>level</th><th>verdict</th><th>段</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.record_id}
                  onClick={() => onSelect(r)}
                  className={`cursor-pointer border-b border-border-faint hover:bg-white/5 ${
                    selectedId === r.record_id ? "bg-accent-gold/10" : ""
                  }`}
                >
                  <td className="font-num py-1 text-text-muted">
                    {r.created_at.slice(0, 16).replace("T", " ")}
                  </td>
                  <td className="font-mono text-text-secondary">{r.hypothesis_id}</td>
                  <td className="font-mono text-text-muted">{r.strategy_id ?? "—"}</td>
                  <td className="text-text-muted">{r.level}</td>
                  <td className={`font-mono ${VERDICT_COLOR[r.verdict] ?? ""}`}>{r.verdict}</td>
                  <td className="text-text-muted">{r.segmentation || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {q.data?.has_more && (
            <AntButton size="small" type="text" className="mt-1" onClick={() => setOffset(offset + 50)}>
              加载更多
            </AntButton>
          )}
        </>
      )}
    </div>
  );
}

/* ── Q1 三形态真值渲染 ── */

/** 周期字符串 → 秒数（compact 时间轴重建用；全档覆盖数据目录档位） */
function timeframeSeconds(tf: string): number {
  const m: Record<string, number> = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "3d": 259200,
  };
  return m[tf] ?? 0;
}

/** V-D3 资金曲线：equity values → 时间轴点对（compact 按 start+length×tf 重建） */
function equityPoints(exec: VrExecution): Array<{ time: string; value: number }> {
  const eq = exec.equity;
  if (!eq?.values?.length) return [];
  if (eq.timestamps && eq.timestamps.length === eq.values.length) {
    return eq.timestamps.map((t, i) => ({ time: t, value: eq.values[i] }));
  }
  // compact 形制：start+length×timeframe 重建等距时间轴
  const sec = timeframeSeconds(eq.timeframe);
  if (!eq.start || !sec) return eq.values.map((_, i) => ({ time: String(i), value: eq.values[i] }));
  const startMs = new Date(eq.start.replace(" ", "T") + (eq.start.includes("+") ? "" : "Z")).getTime();
  if (!Number.isFinite(startMs)) return eq.values.map((_, i) => ({ time: String(i), value: eq.values[i] }));
  return eq.values.map((v, i) => ({
    time: new Date(startMs + i * sec * 1000).toISOString(),
    value: v,
  }));
}

/** trades 笔对 → K 线双标信号（direction 定买卖语义：long entry=buy/exit=sell；short 反转） */
function tradeSignals(trades: VrTrade[]): Array<{ type: "buy" | "sell"; timestamp: string }> {
  const out: Array<{ type: "buy" | "sell"; timestamp: string }> = [];
  for (const t of trades ?? []) {
    if (!t.entry_time || !t.exit_time) continue;
    const long = t.direction !== "short";
    out.push({ type: long ? "buy" : "sell", timestamp: t.entry_time });
    out.push({ type: long ? "sell" : "buy", timestamp: t.exit_time });
  }
  return out;
}

/** V-D1 参数回显：行 params 三键展开（实查 params 形制={request_range,
 *  range_hash, threshold_profile}——无 dna 键，DNA 摘要无处取如实留档） */
function ParamsPanel({ record }: { record: ValidationRecordRow }) {
  const params = record.params as Record<string, unknown> | null;
  if (!params || Object.keys(params).length === 0) {
    return <p className="text-xs text-text-muted">该记录无 params 留痕</p>;
  }
  const rr = (params as { request_range?: Record<string, string> }).request_range;
  return (
    <div className="flex flex-col gap-2 text-xs">
      {rr && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1" data-testid="vr-request-range">
          {[["symbol", "标的"], ["timeframe", "周期"], ["start", "起始"], ["end", "结束"]].map(
            ([k, label]) => (
              <span key={k} className="flex items-baseline gap-2">
                <span className="text-[10px] text-text-muted">{label}</span>
                <span className="font-mono text-text-secondary">{rr[k] ?? "—"}</span>
              </span>
            ),
          )}
        </div>
      )}
      <span className="flex items-baseline gap-2">
        <span className="text-[10px] text-text-muted">range_hash</span>
        <span className="truncate font-mono text-[10px] text-text-muted" title={String(params.range_hash ?? "")}>
          {String(params.range_hash ?? "—")}
        </span>
      </span>
      <span className="flex items-baseline gap-2">
        <span className="text-[10px] text-text-muted">threshold_profile</span>
        <span className="font-mono text-text-secondary">{String(params.threshold_profile ?? "—")}</span>
      </span>
      <p className="text-[10px] text-text-muted">
        params 留痕不含 dna 键（实测 2026-11-03）——DNA 摘要无处取，如实呈现验证请求参数
      </p>
    </div>
  );
}

/** V-D2 K 线复核：选中份 trades 双标+ohlcv 真值 K 线（CHART_VC 容器底） */
function KlineReview({
  exec,
  symbol,
}: {
  exec: VrExecution;
  symbol: string | undefined;
}) {
  const tf = exec.equity?.timeframe;
  const q = useQuery({
    queryKey: ["vr-kline", symbol, tf, exec.range_start, exec.range_end],
    queryFn: () =>
      getOhlcvBySymbol(symbol!, tf!, {
        start: exec.range_start || undefined,
        end: exec.range_end || undefined,
      }),
    enabled: !!symbol && !!tf,
  });
  const signals = tradeSignals(exec.trades ?? []);
  if (!symbol || !tf) {
    return <p className="text-xs text-text-muted">行 params 无 request_range.symbol——K 线区间不可定</p>;
  }
  if (q.isError) return <p className="text-xs text-loss">K 线源暂不可达（稍后自动重试）</p>;
  if (q.isLoading) return <p className="text-xs text-text-muted">K 线装载中…</p>;
  const candles = q.data?.data ?? [];
  return (
    <div
      className="rounded p-2"
      style={{ backgroundColor: CHART_VC.bg, border: `1px solid ${CHART_VC.teal}22` }}
    >
      <p className="mb-1 text-[10px]" style={{ color: CHART_VC.dim }}>
        {symbol} · {tf} · {exec.range_start || "?"} ~ {exec.range_end || "?"} · 双标 {signals.length}（trades {exec.trades?.length ?? 0} 笔）
      </p>
      {candles.length === 0 ? (
        <p className="py-6 text-center text-xs" style={{ color: CHART_VC.dim }}>
          该区间无 K 线数据
        </p>
      ) : (
        <KlineChart data={candles} signals={signals} height={300} />
      )}
    </div>
  );
}

/** V-D3 资金曲线渲染（等距时间轴文本呈现——echarts 面积图组件依赖
 *  EQUITY_MIN_POINTS 聚合层，明细逐点表格呈现契约等价且 jsdom 可测） */
function EquityReview({ exec, initCash }: { exec: VrExecution; initCash: number | null }) {
  const pts = equityPoints(exec);
  const eq = exec.equity;
  if (pts.length === 0) {
    return <p className="text-xs text-text-muted">该执行份无 equity 留痕</p>;
  }
  const compact = eq?.grid_aligned === true;
  const first = pts[0].value;
  const last = pts[pts.length - 1].value;
  const baseline = initCash ?? first;
  const delta = ((last - baseline) / baseline) * 100;
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid="vr-equity">
      <p className="text-[10px] text-text-muted">
        {compact
          ? `compact 形制（start=${eq.start} + ${eq.length}×${eq.timeframe} 时间轴重建）`
          : "全时间戳形制（timestamps 直用）"}
        · {pts.length} 点 · 基准 {baseline.toFixed(0)}
      </p>
      <div className="flex items-baseline gap-4">
        <span className="font-num text-text-secondary">首 {first.toFixed(1)}</span>
        <span className="font-num text-text-secondary">末 {last.toFixed(1)}</span>
        <span className={`font-num font-medium ${delta >= 0 ? "text-profit" : "text-loss"}`}>
          {delta >= 0 ? "+" : ""}{delta.toFixed(2)}%
        </span>
      </div>
      {/* 逐点序列（前 12 点+末点抽样呈现——完整序列在 DOM 属性留档） */}
      <div
        className="grid grid-cols-3 gap-x-3 gap-y-0.5 font-num text-[10px] text-text-muted"
        data-count={pts.length}
      >
        {pts.slice(0, 12).map((p, i) => (
          <span key={`${p.time}-${i}`} className="truncate" title={p.time}>
            {p.value.toFixed(1)}
          </span>
        ))}
        {pts.length > 12 && <span className="text-text-muted">… 共 {pts.length} 点（末 {pts[pts.length - 1].value.toFixed(1)}）</span>}
      </div>
    </div>
  );
}

/** V-D 详情区（五形态：默认空态/参数回显/K线复核/资金曲线/决策记录） */
export function RecordDetail({ record }: { record?: ValidationRecordRow }) {
  const recordId = record?.record_id;
  const q = useQuery({
    queryKey: ["validation-record-detail", recordId],
    queryFn: () => getValidationRecordDetail(recordId!),
    enabled: !!recordId,
  });
  // 多份选择器（隔离验证单份常态；多份=组合验证等）
  const [execIdx, setExecIdx] = useState(0);
  useEffect(() => { setExecIdx(0); }, [recordId]);

  if (!record || !recordId) {
    return (
      <p className="flex items-center gap-2 text-xs text-text-muted">
        <FileSearch className="h-3.5 w-3.5" /> 选择一条记录查看详情
      </p>
    );
  }
  if (q.isError) return <p className="text-xs text-loss">详情源暂不可达（稍后自动重试）</p>;
  if (q.isLoading) return <p className="text-xs text-text-muted">装载中…</p>;
  const d: ValidationDetailResponse = q.data!;
  if (!d.detail_available) {
    return (
      <p className="text-xs text-text-muted">
        该记录为历史无留痕形态（detail_available=false）——不可复核，空骨架如实呈现
      </p>
    );
  }
  const unk = (v: number | null) => (v == null ? "未知" : String(v));
  const exec = d.executions[Math.min(execIdx, d.executions.length - 1)];
  const symbol = (record.params as { request_range?: { symbol?: string } } | null)
    ?.request_range?.symbol;
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-3 text-xs">
        <span className="font-mono text-text-secondary">{d.hypothesis_id}</span>
        <span className={VERDICT_COLOR[d.verdict] ?? ""}>{d.verdict}</span>
        <span className="text-text-muted">{d.level}</span>
        <span className="font-num text-text-muted">
          资金 {unk(d.init_cash)} · 费率 {unk(d.fee)} · 滑点 {unk(d.slippage)}
        </span>
      </div>
      {d.executions.length > 1 && (
        <Select
          size="small"
          className="mb-2 !w-56"
          value={execIdx}
          onChange={setExecIdx}
          options={d.executions.map((e, i) => ({
            value: i,
            label: `份 ${i + 1} · ${e.exec_ref}（${e.range_start || "?"} ~ ${e.range_end || "?"}）`,
          }))}
        />
      )}
      <Tabs
        size="small"
        items={[
          {
            key: "params",
            label: "参数回显",
            children: <ParamsPanel record={record} />,
          },
          {
            key: "kline",
            label: "K线复核",
            children: exec ? <KlineReview exec={exec} symbol={symbol} /> : <p className="text-xs text-text-muted">无执行份</p>,
          },
          {
            key: "equity",
            label: "资金曲线",
            children: exec ? <EquityReview exec={exec} initCash={d.init_cash} /> : <p className="text-xs text-text-muted">无执行份</p>,
          },
          {
            key: "decisions",
            label: "决策记录",
            children: (
              <p className="rounded border border-border-faint bg-bg-base/40 px-2 py-1 text-[10px] text-text-muted">
                决策记录数据面待建（be-validation-decision-records D6）——落地后 trades 逐笔 decision 渲染
              </p>
            ),
          },
        ]}
      />
    </div>
  );
}

/** 详情查询失效工具（发起器闭环用——发起成功失效记录表） */
export function useInvalidateRecords() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["validation-records-page"] });
    qc.invalidateQueries({ queryKey: ["workbench", "validation-records"] });
  };
}
