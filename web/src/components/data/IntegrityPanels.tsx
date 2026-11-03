import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleDashed, RefreshCw } from "lucide-react";
import {
  getMonthlyMatrix,
  getFreshnessStatus,
  type CellStatus,
} from "@/services/dataWorkbench";

/**
 * 09 页完整性月矩阵+保鲜表两区（data-management-revamp 前端组 C1/C2）。
 * REJ-04：缺口=申报制呈现事实（「进开发清单」出口），零修复建议形制。
 */

const STATUS_STYLE: Record<CellStatus, { cls: string; icon: React.ReactNode; label: string }> = {
  green: { cls: "bg-profit/20 text-profit", icon: <CheckCircle2 className="h-3 w-3" />, label: "完整" },
  yellow: { cls: "bg-warning/20 text-warning", icon: <AlertTriangle className="h-3 w-3" />, label: "有缺口" },
  gray: { cls: "bg-white/5 text-text-muted", icon: <CircleDashed className="h-3 w-3" />, label: "无数据" },
};

function StatusDot({ status }: { status: CellStatus }) {
  const s = STATUS_STYLE[status];
  return (
    <span className={`inline-flex h-5 w-5 items-center justify-center rounded ${s.cls}`} title={s.label}>
      {s.icon}
    </span>
  );
}

export function IntegrityMatrix({ symbol, monthsBack }: { symbol: string; monthsBack: number }) {
  const q = useQuery({
    queryKey: ["integrity", "monthly-matrix", symbol, monthsBack],
    queryFn: () => getMonthlyMatrix({ symbol, months_back: monthsBack }),
    refetchInterval: 120_000,
  });

  if (q.isError) {
    return <p className="text-xs text-loss">完整性数据暂不可达（稍后自动重试）</p>;
  }
  if (q.isLoading) return <p className="text-xs text-text-muted">月矩阵装载中…</p>;
  const d = q.data;
  if (!d) return null;

  return (
    <div className="space-y-3" data-testid={`integrity-matrix-${symbol}`}>
      {/* 短周期月矩阵（09-B2r）：月×周期格 */}
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-text-muted">
              <th className="py-1 pr-2 text-left font-normal">月份</th>
              {d.short_period.timeframes.map((tf) => (
                <th key={tf} className="px-1 py-1 font-mono font-normal">{tf}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.short_period.matrix.map((row) => (
              <tr key={row.month} className="border-t border-border-faint">
                <td className="py-1 pr-2 font-mono text-text-secondary">{row.month}</td>
                {row.cells.map((c) => (
                  <td key={c.timeframe} className="px-1 py-1 text-center">
                    <span
                      role="img"
                      aria-label={`${row.month}-${c.timeframe}-${c.status}`}
                      title={`${c.timeframe} ${row.month}：${STATUS_STYLE[c.status].label}，${c.bars_actual} 根${c.gap_count > 0 ? `，缺口 ${c.gap_count} 处` : ""}`}
                    >
                      <StatusDot status={c.status} />
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 长周期整体行（>4h 档整体检查） */}
      {d.long_period.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-wide text-text-muted">长周期档（整体检查）</p>
          {d.long_period.map((row) => (
            <div key={row.timeframe} className="flex items-center gap-2 text-xs" data-testid={`long-${row.timeframe}`}>
              <StatusDot status={row.status} />
              <span className="font-mono text-text-secondary">{row.timeframe}</span>
              <span className="text-text-muted">
                {row.coverage.bars} 根 · {row.coverage.first_bar?.slice(0, 10) ?? "—"} ~ {row.coverage.last_bar?.slice(0, 10) ?? "—"}
                {row.gap_count > 0 && ` · 缺口 ${row.gap_count} 处`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 缺口清单申报制（REJ-04：呈现事实+开发清单出口，零修复建议） */}
      {(() => {
        const gaps = d.short_period.matrix.flatMap((row) =>
          row.cells.filter((c) => c.status !== "green" && c.bars_actual > 0));
        if (gaps.length === 0) return null;
        return (
          <div className="rounded border border-warning/30 bg-warning/5 p-2">
            <p className="text-[11px] text-text-secondary">
              检出 {gaps.length} 个非完整月单元（黄色=有缺口/空值/非法值，灰色=该月无数据）——数据缺口事实已登记，修复动作不在此页提供（申报至开发清单处理）。
            </p>
          </div>
        );
      })()}
    </div>
  );
}



/** 双币并列月矩阵（fe-asset-data-domain 4.3——R9 生效态：双币=前端两次
 *  请求 BTCUSDT/ETHUSDT 并列呈现；单 symbol 输入框退役） */
export function DualIntegrityMatrix({ monthsBack }: { monthsBack: number }) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2" data-testid="dual-integrity">
      <div>
        <p className="mb-1 font-mono text-[11px] text-text-muted">BTCUSDT</p>
        <IntegrityMatrix symbol="BTCUSDT" monthsBack={monthsBack} />
      </div>
      <div>
        <p className="mb-1 font-mono text-[11px] text-text-muted">ETHUSDT</p>
        <IntegrityMatrix symbol="ETHUSDT" monthsBack={monthsBack} />
      </div>
    </div>
  );
}

/** 12 路订阅键全集（2 币 × 6 档——与 core/data/fapi/stream.py
 *  FIXED_SUBSCRIBE_KEYS 同源常量的前端镜像；fe-asset-data-domain 4.3） */
const FRESHNESS_FIXED_KEYS: string[] = [
  "BTCUSDT", "ETHUSDT",
].flatMap((sym) =>
  // F1 修正（t3r5）：FAPI_TIMEFRAMES=1m/5m/15m/1h/4h/1d——30m 是被排除的
  // 超集档（stream.py:76 注释明写），原 30m 为笔误致两格恒「未扫」假占位
  ["1m", "5m", "15m", "1h", "4h", "1d"].map((tf) => `${sym}_${tf}`),
);

/** 保鲜 12 路满格表（4.3）：键集不全时按 FIXED_SUBSCRIBE_KEYS 补「未扫」
 *  占位行（12 路恒满格——补位是展示层完整性非数据伪造，每行状态值如实） */
export function FreshnessTableFixed() {
  const q = useQuery({
    queryKey: ["freshness", "status"],
    queryFn: getFreshnessStatus,
    refetchInterval: 120_000,
  });

  if (q.isError || (q.data && q.data.kline === null && q.data.derivs === null)) {
    return <p className="text-xs text-loss">保鲜守护器未装配（503）——状态不可用</p>;
  }
  if (q.isLoading) return <p className="text-xs text-text-muted">保鲜表装载中…</p>;
  const d = q.data;
  if (!d?.kline) return null;

  const keys = d.kline.keys ?? {};
  // FIXED 键序恒满格：返回键显示实态；FIXED 中未返回者显示「未扫」占位
  const rows = FRESHNESS_FIXED_KEYS.map((key) => ({
    key,
    state: key in keys ? String(keys[key]) : "未扫",
    isPlaceholder: !(key in keys),
  }));
  // 返回键中 FIXED 外的键（超集周期等）尾随如实呈现
  const extras = Object.keys(keys)
    .filter((k) => !FRESHNESS_FIXED_KEYS.includes(k))
    .map((key) => ({ key, state: String(keys[key]), isPlaceholder: false }));

  return (
    <div className="space-y-2" data-testid="freshness-table-fixed">
      <div className="flex items-center gap-2 text-[10px] text-text-muted">
        <RefreshCw className="h-3 w-3" />
        上轮检查 {d.kline.last_run?.replace("T", " ").slice(0, 19) ?? "未运行"}
      </div>
      <div className="grid grid-cols-2 gap-1.5 md:grid-cols-3">
        {[...rows, ...extras].map(({ key, state, isPlaceholder }) => (
          <span
            key={key}
            className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
              isPlaceholder
                ? "border-border-faint bg-white/[0.02] text-text-muted"
                : state === "fresh"
                  ? "border-profit/30 text-profit"
                  : state === "failed" || state === "error"
                    ? "border-loss/30 text-loss"
                    : "border-border-faint text-text-secondary"
            }`}
            title={`${key} · ${state}${isPlaceholder ? "（未扫——守护器键集未覆盖，占位非数据伪造）" : ""}`}
          >
            {key}
            <span className="ml-1 text-text-muted">·{state}</span>
          </span>
        ))}
      </div>
      <p className="text-[10px] text-text-muted">
        12 路 = 2 币 × 6 档订阅键全集（FIXED_SUBSCRIBE_KEYS）；「未扫」=守护器未覆盖该键的展示层占位
      </p>
    </div>
  );
}