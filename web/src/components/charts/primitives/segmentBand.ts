/**
 * 段界标注原语（upgrade-vr-fullrange-chart 契约 B）——时间区间竖带，
 * 仿 positionBand 骨架（canvas 自绘 ISeriesPrimitive），语义独立不扩展：
 * positionBand 承载持仓方向语义（long/short 双色），本原语承载分段验证
 * 的中性段界标注（双色交替仅区分相邻段，无方向含义）。
 * 数据接口 {startTime, endTime, label}：段闭端区间在时间轴上的半透明
 * 竖带+带内顶部段名文字。多带单控制器（一次 setData 全量替换）。
 */
import type {
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  Time,
} from "lightweight-charts";
import { toTime } from "../core/time";
import { TEXT } from "@/theme/tokens";
// fancy-canvas 类型无独立声明——经结构化最小面声明（与 positionBand 同形制）
interface MediaCoordinateSpace {
  context: CanvasRenderingContext2D;
  mediaSize: { width: number; height: number };
}
interface CanvasTarget {
  useMediaCoordinateSpace(cb: (scope: MediaCoordinateSpace) => void): void;
}

export interface SegmentBandData {
  startTime: Time;
  endTime: Time;
  label: string;
}

/** 双色交替（相邻段可区分——8%/14% alpha，视觉验收可微调） */
const BAND_COLORS = ["rgba(59,130,246,0.08)", "rgba(59,130,246,0.14)"] as const;

interface BandCoords {
  x1: number;
  x2: number;
  label: string;
  color: string;
}

class SegmentBandRenderer implements IPrimitivePaneRenderer {
  private readonly bands: BandCoords[];
  constructor(bands: BandCoords[]) {
    this.bands = bands;
  }

  draw(target: CanvasTarget): void {
    if (this.bands.length === 0) return;
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      for (const b of this.bands) {
        ctx.fillStyle = b.color;
        ctx.fillRect(b.x1, 0, Math.max(b.x2 - b.x1, 1), scope.mediaSize.height);
        // 段名文字：带内顶部 10px，带宽不足经 clip 截断
        ctx.save();
        ctx.beginPath();
        ctx.rect(b.x1, 0, Math.max(b.x2 - b.x1, 1), scope.mediaSize.height);
        ctx.clip();
        ctx.fillStyle = TEXT.primary;
        ctx.font = "10px sans-serif";
        ctx.textBaseline = "top";
        ctx.fillText(b.label, b.x1 + 4, 4);
        ctx.restore();
      }
    });
  }
}

class SegmentBandView implements IPrimitivePaneView {
  rendererData: BandCoords[] = [];

  renderer(): IPrimitivePaneRenderer | null {
    return new SegmentBandRenderer(this.rendererData);
  }

  update(bands: SegmentBandData[], chart: SegmentBand["chart"]): void {
    if (!chart) {
      this.rendererData = [];
      return;
    }
    const ts = chart.timeScale();
    const coords: BandCoords[] = [];
    bands.forEach((band, i) => {
      const x1 = ts.timeToCoordinate(band.startTime);
      const x2 = ts.timeToCoordinate(band.endTime);
      // 坐标转换 null（超可视域）该带跳过
      if (x1 === null || x2 === null) return;
      coords.push({ x1, x2, label: band.label, color: BAND_COLORS[i % 2] });
    });
    this.rendererData = coords;
  }
}

export class SegmentBand implements ISeriesPrimitive<Time> {
  private readonly view = new SegmentBandView();
  private chart: import("lightweight-charts").IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private bands: SegmentBandData[] = [];

  setData(bands: SegmentBandData[] | null): void {
    this.bands = bands ?? [];
    this.requestUpdate?.();
  }

  attached(param: { chart: import("lightweight-charts").IChartApi; requestUpdate: () => void }): void {
    this.chart = param.chart;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.requestUpdate = null;
  }

  updateAllViews(): void {
    this.view.update(this.bands, this.chart);
  }

  paneViews(): IPrimitivePaneView[] {
    return [this.view];
  }
}

/** 挂载 helper：attachPrimitive + 多带单控制器（一次 setData 全量替换；
 *  null/[] 全清） */
export function attachSegmentBand(series: ISeriesApi<"Candlestick", Time>): {
  primitive: SegmentBand;
  setData: (bands: SegmentBandData[] | null) => void;
} {
  const primitive = new SegmentBand();
  series.attachPrimitive(primitive);
  return { primitive, setData: (b) => primitive.setData(b) };
}

/** 纯函数层：执行份 → 段带数据（闭端区间；label=exec_ref 段名） */
export function toSegmentBands(
  execs: Array<{ exec_ref: string; range_start: string; range_end: string }>,
): SegmentBandData[] {
  return execs.map((e) => ({
    startTime: toTime(e.range_start),
    endTime: toTime(e.range_end),
    label: e.exec_ref,
  }));
}
