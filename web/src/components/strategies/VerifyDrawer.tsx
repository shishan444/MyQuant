import { useState, useCallback, Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Play, Loader2, Check, X as XIcon } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import { useVerifyStrategies } from "@/hooks/useStrategies";
import { useAvailableSources } from "@/hooks/useDatasets";
import { cn } from "@/lib/utils";
import type { Strategy, VerifyResponse } from "@/types/api";

interface VerifyDrawerProps {
  open: boolean;
  strategies: Strategy[];
  onClose: () => void;
}

interface DateRange {
  start: string;
  end: string;
}

function formatReturn(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(1)}%`;
}

export function VerifyDrawer({ open, strategies, onClose }: VerifyDrawerProps) {
  const [dateRanges, setDateRanges] = useState<DateRange[]>([
    { start: "", end: "" },
    { start: "", end: "" },
    { start: "", end: "" },
  ]);
  const [result, setResult] = useState<VerifyResponse | null>(null);

  const verifyMutation = useVerifyStrategies();
  const { data: sourcesData } = useQuery(useAvailableSources());

  const defaultSymbol = strategies[0]?.symbol ?? "BTCUSDT";
  const defaultTimeframe = strategies[0]?.timeframe ?? "1h";
  const sourceInfo = sourcesData?.sources?.find(
    (s) => s.symbol === defaultSymbol && s.timeframe === defaultTimeframe
  );

  const handleDateChange = useCallback(
    (index: number, field: "start" | "end", value: string) => {
      setDateRanges((prev) => {
        const next = [...prev];
        next[index] = { ...next[index], [field]: value };
        return next;
      });
    },
    []
  );

  const filledRanges = dateRanges.filter((r) => r.start && r.end);
  const canVerify = strategies.length > 0 && filledRanges.length > 0;

  const handleVerify = useCallback(async () => {
    if (!canVerify) return;
    setResult(null);
    verifyMutation.mutate(
      {
        strategy_ids: strategies.map((s) => s.strategy_id),
        data_ranges: filledRanges,
      },
      {
        onSuccess: (data) => setResult(data),
      }
    );
  }, [canVerify, strategies, filledRanges, verifyMutation]);

  const handleClose = useCallback(() => {
    setResult(null);
    setDateRanges([
      { start: "", end: "" },
      { start: "", end: "" },
      { start: "", end: "" },
    ]);
    onClose();
  }, [onClose]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/50" onClick={handleClose} />

      {/* Drawer */}
      <div className="fixed right-0 top-0 z-50 flex h-full w-[720px] max-w-[95vw] flex-col border-l border-slate-700/50 bg-slate-900/95 backdrop-blur-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-700/30 px-5 py-4">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-medium text-slate-200">策略验证</h3>
            <span className="text-[11px] text-slate-500">
              {strategies.length} 条策略 · {defaultSymbol}/{defaultTimeframe}
            </span>
          </div>
          <Button variant="ghost" size="icon-xs" onClick={handleClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Date range inputs */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-slate-400">验证时间区间</h4>
              {sourceInfo && (
                <span className="text-[11px] text-slate-500">
                  数据范围: {sourceInfo.time_start?.slice(0, 10)} ~ {sourceInfo.time_end?.slice(0, 10)}
                </span>
              )}
            </div>
            {dateRanges.map((range, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-[11px] text-slate-500 w-14 shrink-0">
                  区间 {i + 1}
                </span>
                <Input
                  type="date"
                  value={range.start}
                  onChange={(e) => handleDateChange(i, "start", e.target.value)}
                  className="bg-slate-800 border-slate-700/50 text-slate-200 text-xs h-7 flex-1"
                  max={sourceInfo?.time_end?.slice(0, 10)}
                />
                <span className="text-slate-600 text-xs">~</span>
                <Input
                  type="date"
                  value={range.end}
                  onChange={(e) => handleDateChange(i, "end", e.target.value)}
                  className="bg-slate-800 border-slate-700/50 text-slate-200 text-xs h-7 flex-1"
                  max={sourceInfo?.time_end?.slice(0, 10)}
                />
              </div>
            ))}
          </div>

          {/* Strategy count */}
          <div className="text-[11px] text-slate-500">
            已选 {strategies.length} 条策略 · 至少填写 1 个时间区间即可验证
          </div>

          {/* Verify button */}
          <Button
            onClick={handleVerify}
            disabled={!canVerify || verifyMutation.isPending}
            className="w-full h-9"
          >
            {verifyMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                验证中...
              </>
            ) : (
              <>
                <Play className="w-4 h-4 mr-2" />
                开始验证
              </>
            )}
          </Button>

          {/* Results */}
          {result && (
            <div className="space-y-3">
              <h4 className="text-xs font-medium text-slate-400">
                验证结果（按综合评分降序）
              </h4>

              {result.summary.length === 0 ? (
                <div className="text-xs text-slate-500 text-center py-4">
                  无有效结果
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-slate-700/40">
                        <th className="py-2 text-left text-slate-500 font-medium sticky left-0 bg-slate-900/95 z-10 min-w-[100px]">
                          策略名
                        </th>
                        {result.summary[0]?.per_period_metrics.map((_, pi) => (
                          <th
                            key={pi}
                            className="py-2 text-center text-slate-500 font-medium"
                            colSpan={3}
                          >
                            <span>区间 {pi + 1}</span>
                            {result.summary[0]?.per_period_metrics[pi] && (
                              <span className="block text-slate-600 font-normal">
                                {result.summary[0].per_period_metrics[pi].data_start.slice(0, 10)}~
                                {result.summary[0].per_period_metrics[pi].data_end.slice(0, 10)}
                              </span>
                            )}
                          </th>
                        ))}
                        <th className="py-2 text-center text-slate-500 font-medium min-w-[70px]">
                          综合评分
                        </th>
                        <th className="py-2 text-center text-slate-500 font-medium min-w-[50px]">
                          达标率
                        </th>
                      </tr>
                      <tr className="border-b border-slate-700/20">
                        <th className="py-1 sticky left-0 bg-slate-900/95 z-10" />
                        {result.summary[0]?.per_period_metrics.map((_, pi) => (
                          <Fragment key={`h-${pi}`}>
                            <th className="py-1 text-center text-slate-600 font-normal">年化</th>
                            <th className="py-1 text-center text-slate-600 font-normal">夏普</th>
                            <th className="py-1 text-center text-slate-600 font-normal">达标</th>
                          </Fragment>
                        ))}
                        <th className="py-1" />
                        <th className="py-1" />
                      </tr>
                    </thead>
                    <tbody>
                      {result.summary.map((item) => (
                        <tr
                          key={item.strategy_id}
                          className="border-b border-slate-700/10 hover:bg-slate-800/50"
                        >
                          <td className="py-2 text-slate-300 truncate max-w-[100px] sticky left-0 bg-slate-900/95 z-10">
                            {item.strategy_name}
                          </td>
                          {item.per_period_metrics.map((pm, pi) => (
                            <Fragment key={`${item.strategy_id}-${pi}`}>
                              <td
                                className={cn(
                                  "py-2 text-center font-mono tabular-nums",
                                  pm.total_return > 0
                                    ? "text-emerald-400"
                                    : pm.total_return < 0
                                      ? "text-red-400"
                                      : "text-slate-500"
                                )}
                              >
                                {formatReturn(pm.total_return)}
                              </td>
                              <td
                                className={cn(
                                  "py-2 text-center font-mono tabular-nums",
                                  pm.sharpe_ratio > 0 ? "text-slate-300" : "text-slate-500"
                                )}
                              >
                                {pm.sharpe_ratio.toFixed(2)}
                              </td>
                              <td className="py-2 text-center">
                                {pm.qualified ? (
                                  <Check className="w-3 h-3 text-emerald-400 inline" />
                                ) : (
                                  <XIcon className="w-3 h-3 text-red-400/60 inline" />
                                )}
                              </td>
                            </Fragment>
                          ))}
                          <td className="py-2 text-center font-mono font-bold text-slate-200 tabular-nums">
                            {item.comprehensive_score.toFixed(2)}
                          </td>
                          <td className="py-2 text-center text-slate-400">
                            {item.qualified_count}/{item.total_periods}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
