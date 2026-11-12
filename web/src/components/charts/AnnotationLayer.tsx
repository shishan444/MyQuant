/** 图表标注的 Canvas 覆盖层（水平线/矩形框）——绘制交互与视口联动重绘。 */
import { useEffect, useRef, useCallback } from "react";
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";

export interface Annotation {
  id: string;
  type: "line" | "box";
  price?: number;
  timeStart?: number;
  timeEnd?: number;
  priceEnd?: number;
  label?: string;
}

interface AnnotationLayerProps {
  chartApi: IChartApi | null;
  candleSeries: ISeriesApi<"Candlestick"> | null;
  annotations: Annotation[];
  activeTool: "line" | "box" | null;
  onAnnotationComplete: (annotation: Annotation) => void;
  chartContainerRef: React.RefObject<HTMLDivElement | null>;
}

export function AnnotationLayer({
  chartApi,
  candleSeries,
  annotations,
  activeTool,
  onAnnotationComplete,
  chartContainerRef,
}: AnnotationLayerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawingRef = useRef<{ startX: number; startY: number } | null>(null);
  const rafRef = useRef<number>(0);

  // 图表平移/缩放时重绘标注
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const chart = chartApi;
    const series = candleSeries;
    if (!canvas || !chart || !series) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const container = chartContainerRef.current;
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    ctx.clearRect(0, 0, rect.width, rect.height);

    for (const ann of annotations) {
      if (ann.type === "line" && ann.price != null) {
        const y = series.priceToCoordinate(ann.price);
        if (y === null) continue;

        ctx.beginPath();
        ctx.strokeStyle = "rgba(251, 191, 36, 0.7)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.moveTo(0, y);
        ctx.lineTo(rect.width, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // Label
        ctx.fillStyle = "rgba(251, 191, 36, 0.9)";
        ctx.font = "11px monospace";
        ctx.fillText(
          `${ann.label ?? ann.price.toFixed(2)}`,
          4,
          y - 4,
        );
      }

      if (ann.type === "box" && ann.timeStart != null && ann.price != null
          && ann.timeEnd != null && ann.priceEnd != null) {
        const x1 = chart.timeScale().timeToCoordinate(ann.timeStart as Time);
        const x2 = chart.timeScale().timeToCoordinate(ann.timeEnd as Time);
        const y1 = series.priceToCoordinate(ann.price);
        const y2 = series.priceToCoordinate(ann.priceEnd);

        if (x1 == null || x2 == null || y1 == null || y2 == null) continue;

        ctx.beginPath();
        ctx.strokeStyle = "rgba(139, 92, 246, 0.6)";
        ctx.fillStyle = "rgba(139, 92, 246, 0.1)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.rect(
          Math.min(x1, x2),
          Math.min(y1, y2),
          Math.abs(x2 - x1),
          Math.abs(y2 - y1),
        );
        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }, [chartApi, candleSeries, annotations, chartContainerRef]);

  // 订阅可见区间变化触发重绘
  useEffect(() => {
    if (!chartApi) return;

    const handler = () => {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(redraw);
    };

    chartApi.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return () => {
      chartApi.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
      cancelAnimationFrame(rafRef.current);
    };
  }, [chartApi, redraw]);

  // 标注集变化时重绘
  useEffect(() => {
    redraw();
  }, [redraw]);

  // 绘制用鼠标事件处理器
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!activeTool) return;
    const rect = e.currentTarget.getBoundingClientRect();
    drawingRef.current = { startX: e.clientX - rect.left, startY: e.clientY - rect.top };
  }, [activeTool]);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!activeTool || !drawingRef.current || !candleSeries || !chartApi) return;

    const rect = e.currentTarget.getBoundingClientRect();
    const endX = e.clientX - rect.left;
    const endY = e.clientY - rect.top;
    const { startX, startY } = drawingRef.current;
    drawingRef.current = null;

    if (activeTool === "line") {
      const price = candleSeries.coordinateToPrice(endY);
      if (price !== null) {
        onAnnotationComplete({
          id: `line-${Date.now()}`,
          type: "line",
          price,
          label: price.toFixed(2),
        });
      }
    }

    if (activeTool === "box") {
      const time1 = chartApi.timeScale().coordinateToTime(Math.min(startX, endX));
      const time2 = chartApi.timeScale().coordinateToTime(Math.max(startX, endX));
      const price1 = candleSeries.coordinateToPrice(Math.min(startY, endY));
      const price2 = candleSeries.coordinateToPrice(Math.max(startY, endY));

      if (time1 != null && time2 != null && price1 != null && price2 != null) {
        onAnnotationComplete({
          id: `box-${Date.now()}`,
          type: "box",
          timeStart: time1 as number,
          timeEnd: time2 as number,
          price: price1,
          priceEnd: price2,
        });
      }
    }
  }, [activeTool, candleSeries, chartApi, onAnnotationComplete]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 z-10"
      style={{
        pointerEvents: activeTool ? "auto" : "none",
        cursor: activeTool === "line" ? "crosshair" : activeTool === "box" ? "crosshair" : "default",
      }}
      onMouseDown={handleMouseDown}
      onMouseUp={handleMouseUp}
      role="img"
      aria-label="Chart annotation layer"
    />
  );
}
