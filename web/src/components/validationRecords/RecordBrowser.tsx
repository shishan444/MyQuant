/**
 * 验证记录浏览组件族（fe-validation-records-page）——页面级私有（契约三）。
 * 骨架：V-S 筛选栏→V-T 记录表→V-D0~D4 详情五形态。
 * 分页语义沿端点契约：level/verdict 行级过滤不进 total（前端同口径显示——如实披露）。
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button as AntButton, Select, Input, Tabs } from "antd";
import { FileSearch, RotateCcw, Search } from "lucide-react";
import {
  getValidationRecordDetail,
  getValidationRecordsPage,
  VALIDATION_LEVELS,
  VALIDATION_VERDICTS,
  type ValidationRecordRow,
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
    id: string;
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
      id: trimmed || undefined as unknown as string,
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

/** V-T 记录表（limit 50+加载更多 has_more 驱动） */
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

/** V-D 详情区（五形态：默认空态/参数回显/K线复核/资金曲线/决策记录） */
export function RecordDetail({ recordId }: { recordId?: string }) {
  const q = useQuery({
    queryKey: ["validation-record-detail", recordId],
    queryFn: () => getValidationRecordDetail(recordId!),
    enabled: !!recordId,
  });
  if (!recordId) {
    return (
      <p className="flex items-center gap-2 text-xs text-text-muted">
        <FileSearch className="h-3.5 w-3.5" /> 选择一条记录查看详情
      </p>
    );
  }
  if (q.isError) return <p className="text-xs text-loss">详情源暂不可达（稍后自动重试）</p>;
  if (q.isLoading) return <p className="text-xs text-text-muted">装载中…</p>;
  const d = q.data!;
  if (!d.detail_available) {
    return (
      <p className="text-xs text-text-muted">
        该记录为历史无留痕形态（detail_available=false）——不可复核，空骨架如实呈现
      </p>
    );
  }
  const unk = (v: number | null) => (v == null ? "未知" : String(v));
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
      <Tabs
        size="small"
        items={[
          {
            key: "params",
            label: "参数回显",
            children: <p className="text-xs text-text-muted">参数快照区（记录 params JSON 展开——DNA 摘要随 params 内容物形制呈现）</p>,
          },
          {
            key: "kline",
            label: "K线复核",
            children: <p className="text-xs text-text-muted">K 线复核区（executions trades 信号标记+CHART_VC 色板——executions 有而 trades 空则无标记如实）</p>,
          },
          {
            key: "equity",
            label: "资金曲线",
            children: <p className="text-xs text-text-muted">资金曲线区（executions equity 序列；compact 形制按 start+length×timeframe 重建时间轴）</p>,
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
