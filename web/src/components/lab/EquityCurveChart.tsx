import { useEffect, useRef, useState } from "react";
import { toTime } from "../charts/core/time";

interface EquityCurveChartProps {
  data: Array<{ timestamp: string; value: number }>;
  height?: number;
}

export function EquityCurveChart({ data, height = 180 }: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!containerRef.current || data.length < 2) return;

    let chart: import("lightweight-charts").IChartApi | null = null;
    let cancelled = false;

    import("lightweight-charts")
      .then(({ createChart, LineSeries, ColorType }) => {
        if (cancelled || !containerRef.current) return;

        const { width } = containerRef.current.getBoundingClientRect();
        chart = createChart(containerRef.current, {
          width: width || 600,
          height,
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
            time: toTime(d.timestamp),
            value: d.value,
          }));

        lineSeries.setData(sortedData);
        chart.timeScale().fitContent();
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    return () => {
      cancelled = true;
      chart?.remove();
    };
  }, [data, height]);

  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center text-xs text-slate-500" style={{ height }}>
        数据不足，无法绘制资金曲线
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center text-xs text-slate-500" style={{ height }}>
        图表加载失败
      </div>
    );
  }

  return <div ref={containerRef} />;
}
