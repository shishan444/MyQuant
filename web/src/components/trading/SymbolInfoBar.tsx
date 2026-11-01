import { useMemo } from "react";
import type { CandleData } from "@/components/charts/KlineChart";

/**
 * 交易对信息栏（kline-common-layer 组 2）——07 页 K 线区顶部横条。
 * 数据面=诚实子集：最新价与装载窗聚合高/低/量从已装载 ohlcv 现算（窗=装载 200 根 bar），
 * 累计资金费直读任务 cumulative_funding 字段；标记价/指数价/持仓量后端无数据不呈现
 * （web-ui-truthfulness：不画假数据，待行情源 change 补）。
 */
interface SymbolInfoBarProps {
  symbol: string;
  timeframe: string;
  candles: CandleData[];
  cumulativeFunding?: number | null;
}

function fmt(n: number, digits = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function SymbolInfoBar({ symbol, timeframe, candles, cumulativeFunding }: SymbolInfoBarProps) {
  const stats = useMemo(() => {
    if (!candles.length) return null;
    const last = candles[candles.length - 1];
    const first = candles[0];
    const high = Math.max(...candles.map((c) => c.high));
    const low = Math.min(...candles.map((c) => c.low));
    const vol = candles.reduce((sum, c) => sum + ((c as { volume?: number }).volume ?? 0), 0);
    const change = first.open !== 0 ? (last.close - first.open) / first.open : 0;
    return { last: last.close, high, low, vol, change };
  }, [candles]);

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-border-default px-3 py-2 text-xs">
      <span className="font-mono text-sm font-semibold text-text-primary">{symbol}</span>
      <span className="text-[11px] text-text-muted">{timeframe}</span>
      {stats ? (
        <>
          <span
            className="font-mono text-base font-semibold"
            style={{ color: stats.change >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}
            data-testid="symbol-last-price"
          >
            {fmt(stats.last)}
          </span>
          <span className="font-mono" style={{ color: stats.change >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>
            {stats.change >= 0 ? "+" : ""}
            {(stats.change * 100).toFixed(2)}%
          </span>
          <span className="text-text-secondary">
            装载窗高 <span className="font-mono text-text-primary">{fmt(stats.high)}</span>
          </span>
          <span className="text-text-secondary">
            装载窗低 <span className="font-mono text-text-primary">{fmt(stats.low)}</span>
          </span>
          {stats.vol > 0 && (
            <span className="text-text-secondary">
              装载窗量 <span className="font-mono text-text-primary">{fmt(stats.vol, 0)}</span>
            </span>
          )}
        </>
      ) : (
        <span className="text-text-muted">行情装载中…</span>
      )}
      {cumulativeFunding != null && (
        <span className="text-text-secondary">
          累计资金费{" "}
          <span className="font-mono text-text-primary">
            {cumulativeFunding >= 0 ? "+" : ""}
            {fmt(cumulativeFunding, 4)} USDT
          </span>
        </span>
      )}
    </div>
  );
}
