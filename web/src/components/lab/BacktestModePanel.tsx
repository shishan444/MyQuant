import { useState, useMemo, useRef, useImperativeHandle, forwardRef } from "react";
import { Loader2, AlertTriangle, Dna } from "lucide-react";
import { toast } from "sonner";
import { useNavigate } from "react-router";
import { Button as AntButton } from "antd";
import { KlineChart } from "@/components/charts/KlineChart";
import type { KlineChartHandle, SignalData } from "@/components/charts/KlineChart";
import { BacktestMetricsPanel } from "./BacktestMetricsPanel";
import { EquityCurve } from "@/components/charts/EquityCurve";
import { runBacktest } from "@/services/strategies";
import { useCreateStrategy } from "@/hooks/useStrategies";
import { useChartIndicators } from "@/hooks/useChartIndicators";
import type { SubChartType } from "@/hooks/useChartIndicators";
import { StrategyDetail } from "@/components/StrategyDetail";
import type { DNA, BacktestResult, TradeSignal } from "@/types/api";
import { cn } from "@/lib/utils";

export interface BacktestModePanelHandle {
  runBacktest: () => void;
}

interface BacktestModePanelProps {
  dna: DNA;
  symbol: string;
  timeframe: string;
  dataStart?: string;
  dataEnd?: string;
  fee?: number;
  slippage?: number;
  initCash?: number;
}

export const BacktestModePanel = forwardRef<BacktestModePanelHandle, BacktestModePanelProps>(
  function BacktestModePanel(
    {
      dna,
      symbol,
      timeframe,
      dataStart,
      dataEnd,
      fee = 0.001,
      slippage = 0.0005,
      initCash = 100000,
    },
    ref,
  ) {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<BacktestResult | null>(null);
    const [ohlcvWarning, setOhlcvWarning] = useState<string | null>(null);
    const [subChartType, setSubChartType] = useState<SubChartType>("volume");
    const chartRef = useRef<KlineChartHandle>(null);
    const saveStrategy = useCreateStrategy();
    const navigate = useNavigate();

    const datasetId = `${symbol}_${timeframe}`;

    // Fetch chart indicators (only after backtest has a result)
    const {
      candleData,
      chartIndicators,
      chartBollData,
      volumeData,
      macdData,
      kdjData,
    } = useChartIndicators({
      symbol,
      timeframe,
      dateRange: { start: dataStart, end: dataEnd },
      subChartType,
      enabled: !!result,
    });

    function runBacktestAction() {
      setLoading(true);
      setError(null);
      setResult(null);
      setOhlcvWarning(null);

      // Apply leverage override to DNA copy
      const dnaCopy: DNA = {
        ...dna,
        risk_genes: { ...dna.risk_genes },
      };

      runBacktest({
        dna: dnaCopy,
        symbol,
        timeframe,
        dataset_id: datasetId,
        data_start: dataStart || undefined,
        data_end: dataEnd || undefined,
        fee,
        slippage,
        init_cash: initCash,
      })
        .then((btResult) => {
          setResult(btResult);
        })
        .catch((err: unknown) => {
          const msg = err instanceof Error ? err.message : "回测失败";
          setError(msg);
        })
        .finally(() => setLoading(false));
    }

    // Expose runBacktestAction to parent via ref (useRef thunk to avoid stale closure)
    const latestRunBacktest = useRef(runBacktestAction);
    latestRunBacktest.current = runBacktestAction;

    useImperativeHandle(ref, () => ({
      runBacktest: () => latestRunBacktest.current(),
    }), []);

    const signals: SignalData[] = useMemo(() => {
      if (!result?.signals) return [];
      return result.signals.map((s: TradeSignal) => ({
        type: s.type,
        timestamp: s.timestamp,
      }));
    }, [result]);

    const equityPoints = useMemo(() => {
      if (!result?.equity_curve) return [];
      return result.equity_curve;
    }, [result]);

    async function handleSave() {
      try {
        await saveStrategy.mutateAsync({
          name: `${symbol} ${timeframe} 回测策略`,
          dna,
          symbol,
          timeframe,
          source: "lab",
          tags: "backtest,evolution",
        });
        toast.success("策略已保存");
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "保存失败");
      }
    }

    if (loading) {
      return (
        <div className="flex flex-col items-center justify-center gap-3 py-20">
          <Loader2 className="h-8 w-8 animate-spin text-amber-400" />
          <span className="text-sm text-slate-400">正在运行回测...</span>
        </div>
      );
    }

    if (error) {
      return (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-red-500/20 bg-red-500/5 p-8">
          <AlertTriangle className="h-8 w-8 text-red-400" />
          <p className="text-sm text-red-400">{error}</p>
          <AntButton size="small" onClick={runBacktestAction}>
            重试
          </AntButton>
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-5">
        {/* DNA summary */}
        <div>
          <h4 className="mb-2 text-xs font-medium text-slate-400">策略基因</h4>
          <StrategyDetail dna={dna} />
        </div>

        {result && (
          <>
            {/* OHLCV warning */}
            {ohlcvWarning && (
              <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {ohlcvWarning}
              </div>
            )}

            {/* K-line chart with trade signals + indicators */}
            {candleData && candleData.length > 0 && (
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <h4 className="text-xs font-medium text-slate-400">
                    K 线 + 交易信号
                    <span className="ml-2 text-slate-600">
                      {symbol} / {timeframe}
                    </span>
                  </h4>
                  {/* Sub-chart indicator selector */}
                  <div className="flex items-center gap-0.5">
                    <span className="mr-1 text-[11px] text-slate-500">副图</span>
                    {(["volume", "macd", "rsi", "kdj", "equity"] as const).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setSubChartType(t)}
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[11px] transition-colors",
                          subChartType === t
                            ? "bg-sky-400/20 text-sky-400"
                            : "bg-slate-800/30 text-slate-500 hover:text-slate-400",
                        )}
                      >
                        {t.toUpperCase()}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="rounded-lg border border-slate-700/30 bg-slate-950/50">
                  <KlineChart
                    ref={chartRef}
                    data={candleData.map((d) => ({
                      timestamp: d.timestamp,
                      open: d.open,
                      high: d.high,
                      low: d.low,
                      close: d.close,
                    }))}
                    signals={signals}
                    indicators={chartIndicators}
                    bollData={chartBollData}
                    volumeData={volumeData}
                    subChartType={subChartType}
                    macdData={macdData}
                    kdjData={kdjData}
                    equityData={equityPoints.map((p) => ({
                      time: p.timestamp,
                      value: p.value,
                    }))}
                    height={420}
                  />
                </div>
                <div className="mt-1.5 flex items-center gap-4 text-[10px] text-slate-500">
                  <span className="flex items-center gap-1">
                    <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
                    买入 ({signals.filter((s) => s.type === "buy").length})
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block h-2 w-2 rounded-full bg-red-400" />
                    卖出 ({signals.filter((s) => s.type === "sell").length})
                  </span>
                  <span className="ml-auto">
                    {result.data_start?.slice(0, 10)} ~ {result.data_end?.slice(0, 10)}
                  </span>
                </div>
              </div>
            )}

            {/* Equity curve */}
            {equityPoints.length > 0 && (
              <div>
                <h4 className="mb-2 text-xs font-medium text-slate-400">资金曲线</h4>
                <div className="rounded-lg border border-slate-700/30 bg-slate-950/50 p-3">
                  <EquityCurve data={equityPoints} height={180} />
                </div>
                <div className="mt-1.5 flex items-center gap-4 text-[10px] text-slate-500">
                  <span>初始: {equityPoints[0]?.value?.toLocaleString()}</span>
                  <span>最终: {equityPoints[equityPoints.length - 1]?.value?.toLocaleString()}</span>
                  {result.liquidated && <span className="text-red-400">已爆仓</span>}
                </div>
              </div>
            )}

            {/* Metrics panel */}
            <div>
              <h4 className="mb-2 text-xs font-medium text-slate-400">回测指标</h4>
              <BacktestMetricsPanel result={result} />
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2">
              <AntButton size="small" onClick={runBacktestAction} className="!text-[11px]">
                重新运行
              </AntButton>
              <AntButton
                size="small"
                onClick={handleSave}
                disabled={saveStrategy.isPending}
                className="!text-[11px]"
                style={{ color: "#fbbf24" }}
              >
                保存策略
              </AntButton>
              <AntButton
                size="small"
                onClick={() => navigate("/evolution", { state: { seedDna: dna } })}
                className="!text-[11px]"
                style={{ color: "#c084fc" }}
              >
                <Dna className="h-3.5 w-3.5" />
                继续优化
              </AntButton>
            </div>
          </>
        )}
      </div>
    );
  },
);
