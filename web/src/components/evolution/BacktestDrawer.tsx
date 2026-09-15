import { useState, useEffect, useRef, useMemo } from "react";
import { X, Loader2, AlertTriangle } from "lucide-react";
import { Button as AntButton } from "antd";
import { cn, formatNumber, getFitnessColor } from "@/lib/utils";
import { KlineChart } from "@/components/charts/KlineChart";
import type { KlineChartHandle, CandleData, SignalData } from "@/components/charts/KlineChart";
import { runBacktest } from "@/services/strategies";
import { getOhlcvBySymbol } from "@/services/datasets";
import type { DNA, BacktestResult, TradeSignal } from "@/types/api";

interface BacktestDrawerProps {
  dna: DNA;
  symbol: string;
  timeframe: string;
  dataStart?: string;
  dataEnd?: string;
  open: boolean;
  onClose: () => void;
}

export function BacktestDrawer({
  dna,
  symbol,
  timeframe,
  dataStart,
  dataEnd,
  open,
  onClose,
}: BacktestDrawerProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const chartRef = useRef<KlineChartHandle>(null);

  useEffect(() => {
    if (!open) {
      setResult(null);
      setCandles([]);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const datasetId = `${symbol}_${timeframe}`;

    Promise.all([
      runBacktest({
        dna,
        symbol,
        timeframe,
        dataset_id: datasetId,
      }),
      getOhlcvBySymbol(symbol, timeframe, {
        start: dataStart || undefined,
        end: dataEnd || undefined,
        limit: 10000,
      }).catch(() => null),
    ])
      .then(([btResult, ohlcvData]) => {
        if (cancelled) return;
        setResult(btResult);

        if (ohlcvData?.data) {
          const mapped: CandleData[] = ohlcvData.data.map((d) => ({
            timestamp: d.timestamp,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
          }));
          setCandles(mapped);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : "回测失败";
        setError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, dna, symbol, timeframe, dataStart, dataEnd]);

  // Map TradeSignal[] to SignalData[] for KlineChart
  const signals: SignalData[] = useMemo(() => {
    if (!result?.signals) return [];
    return result.signals.map((s: TradeSignal) => ({
      type: s.type,
      timestamp: s.timestamp,
    }));
  }, [result]);

  // Equity curve data
  const equityPoints = useMemo(() => {
    if (!result?.equity_curve) return [];
    return result.equity_curve;
  }, [result]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} />

      {/* Drawer */}
      <div className="fixed right-0 top-0 z-50 flex h-full w-[720px] max-w-[95vw] flex-col border-l border-slate-700/50 bg-slate-900/95 backdrop-blur-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-700/30 px-5 py-4">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-medium text-slate-200">
              可视化验证
            </h3>
            <span className="text-[11px] text-slate-500">
              {symbol} / {timeframe}
            </span>
          </div>
          <AntButton type="text" size="small" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </AntButton>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-20">
              <Loader2 className="h-8 w-8 animate-spin text-amber-400" />
              <span className="text-sm text-slate-400">正在运行回测...</span>
            </div>
          )}

          {error && (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-8">
              <AlertTriangle className="h-8 w-8 text-red-400" />
              <p className="text-sm text-red-400">{error}</p>
              <AntButton size="small" onClick={onClose}>
                关闭
              </AntButton>
            </div>
          )}

          {!loading && !error && result && (
            <div className="flex flex-col gap-5">
              {/* K-line chart with signals */}
              {candles.length > 0 && (
                <div>
                  <h4 className="mb-2 text-xs font-medium text-slate-400">
                    K 线 + 交易信号
                  </h4>
                  <div className="rounded-lg border border-slate-700/30 bg-slate-950/50">
                    <KlineChart
                      ref={chartRef}
                      data={candles}
                      signals={signals}
                      height={380}
                    />
                  </div>
                  <div className="mt-1.5 flex items-center gap-4 text-[10px] text-slate-500">
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
                      买入信号 ({signals.filter((s) => s.type === "buy").length})
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="inline-block h-2 w-2 rounded-full bg-red-400" />
                      卖出信号 ({signals.filter((s) => s.type === "sell").length})
                    </span>
                  </div>
                </div>
              )}

              {/* Equity curve */}
              {equityPoints.length > 0 && (
                <div>
                  <h4 className="mb-2 text-xs font-medium text-slate-400">
                    资金曲线
                  </h4>
                  <div className="rounded-lg border border-slate-700/30 bg-slate-950/50 p-3">
                    <EquityCurve data={equityPoints} />
                  </div>
                  <div className="mt-1.5 flex items-center gap-4 text-[10px] text-slate-500">
                    <span>初始: {formatNumber(equityPoints[0]?.value ?? 100000)}</span>
                    <span>最终: {formatNumber(equityPoints[equityPoints.length - 1]?.value ?? 0)}</span>
                    {result.liquidated && (
                      <span className="text-red-400">已爆仓</span>
                    )}
                  </div>
                </div>
              )}

              {/* Metrics panel */}
              <MetricsPanel result={result} />
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Equity curve (lightweight line chart)
// ---------------------------------------------------------------------------

function EquityCurve({ data }: { data: Array<{ timestamp: string; value: number }> }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || data.length < 2) return;

    let chart: import("lightweight-charts").IChartApi | null = null;

    import("lightweight-charts").then(({ createChart, LineSeries, ColorType }) => {
      if (!containerRef.current) return;

      const { width } = containerRef.current.getBoundingClientRect();
      chart = createChart(containerRef.current, {
        width,
        height: 180,
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: "#94a3b8",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: "rgba(51,65,85,0.2)" },
          horzLines: { color: "rgba(51,65,85,0.2)" },
        },
        rightPriceScale: { borderColor: "rgba(51,65,85,0.3)" },
        timeScale: { borderColor: "rgba(51,65,85,0.3)", timeVisible: true },
        crosshair: { mode: 0 },
      });

      const lineSeries = chart.addSeries(LineSeries, {
        color: "#f59e0b",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      });

      const sortedData = [...data]
        .sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
        .map((d) => ({
          time: d.timestamp.slice(0, 19) as import("lightweight-charts").Time,
          value: d.value,
        }));

      lineSeries.setData(sortedData);
      chart.timeScale().fitContent();
    });

    return () => {
      chart?.remove();
    };
  }, [data]);

  return <div ref={containerRef} />;
}

// ---------------------------------------------------------------------------
// Metrics panel
// ---------------------------------------------------------------------------

function MetricsPanel({ result }: { result: BacktestResult }) {
  const items = [
    {
      label: "总收益",
      value: `${(result.total_return * 100).toFixed(2)}%`,
      color: result.total_return > 0 ? "text-emerald-400" : "text-red-400",
    },
    {
      label: "夏普比率",
      value: formatNumber(result.sharpe_ratio),
      color: result.sharpe_ratio > 1 ? "text-emerald-400" : result.sharpe_ratio > 0 ? "text-amber-400" : "text-red-400",
    },
    {
      label: "最大回撤",
      value: `${(result.max_drawdown * 100).toFixed(2)}%`,
      color: Math.abs(result.max_drawdown) < 0.1 ? "text-emerald-400" : Math.abs(result.max_drawdown) < 0.3 ? "text-amber-400" : "text-red-400",
    },
    {
      label: "胜率",
      value: `${(result.win_rate * 100).toFixed(1)}%`,
      color: result.win_rate > 0.5 ? "text-emerald-400" : result.win_rate > 0.3 ? "text-amber-400" : "text-red-400",
    },
    {
      label: "交易次数",
      value: String(result.total_trades),
      color: result.total_trades > 10 ? "text-slate-200" : "text-amber-400",
    },
    {
      label: "适应度",
      value: result.fitness != null ? result.fitness.toFixed(2) : "-",
      color: result.qualified ? "text-emerald-400" : result.fitness != null ? getFitnessColor(result.fitness) : "text-slate-500",
    },
  ];

  return (
    <div>
      <h4 className="mb-2 text-xs font-medium text-slate-400">回测指标</h4>
      <div className="grid grid-cols-3 gap-2">
        {items.map((item) => (
          <div
            key={item.label}
            className="rounded-lg border border-slate-700/30 bg-white/[0.02] p-3 text-center"
          >
            <div className="text-[11px] text-slate-500">{item.label}</div>
            <div className={cn("mt-1 text-sm font-mono", item.color)}>
              {item.value}
            </div>
          </div>
        ))}
      </div>
      {result.qualified != null && (
        <div className="mt-2 flex items-center gap-2 text-[11px]">
          <span className={result.qualified ? "text-emerald-400" : "text-slate-500"}>
            {result.qualified ? "达标" : "未达标"}
          </span>
          <span className={cn(
            "inline-block h-2 w-2 rounded-full",
            result.qualified ? "bg-emerald-400" : "bg-slate-600"
          )} />
        </div>
      )}
      {result.total_funding_cost > 0 && (
        <div className="mt-2 text-[11px] text-amber-500/80">
          资金费用: {formatNumber(result.total_funding_cost)}
        </div>
      )}
      {result.liquidated && (
        <div className="mt-1 text-[11px] text-red-400">
          策略触发爆仓，仓位已强制平仓
        </div>
      )}
    </div>
  );
}
