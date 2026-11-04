import type {
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneView,
  IPrimitivePaneRenderer,
  Time,
  IChartApi,
} from "lightweight-charts";
import { toTime } from "../core/time";
// fancy-canvas 类型无独立声明——经结构化最小面声明（draw 目标仅用 useMediaCoordinateSpace）
interface MediaCoordinateSpace {
  context: CanvasRenderingContext2D;
  mediaSize: { width: number; height: number };
}
interface CanvasTarget {
  useMediaCoordinateSpace(cb: (scope: MediaCoordinateSpace) => void): void;
}

/**
 * G-标注原语 · 持仓带（kline-common-layer 组 4）——时间区间竖带。
 * 数据接口 {entryTime, exitTime?, side}：持仓存活区间在时间轴上的半透明竖带
 * （long=绿系/short=红系；exitTime 缺省=持仓中，带延伸至图右缘）。
 * C5 模拟（07 页）与 C4 假设工作台共用的共享资产。
 */
export interface PositionBandData {
  entryTime: Time;
  exitTime?: Time;
  side: "long" | "short";
}

const BAND_COLOR: Record<PositionBandData["side"], string> = {
  long: "rgba(45,212,191,0.08)" /* T2：POLARITY.up 8% alpha 衍生 */,
  short: "rgba(251,113,133,0.08)" /* T2：POLARITY.down 8% alpha 衍生 */,
};

interface BandCoords {
  x1: number;
  x2: number | null; // null=延伸至右缘
  color: string;
}

class PositionBandRenderer implements IPrimitivePaneRenderer {
  private readonly data: BandCoords | null;
  constructor(data: BandCoords | null) {
    this.data = data;
  }

  draw(target: CanvasTarget): void {
    if (!this.data) return;
    // media 坐标空间：x1/x2 本就是 timeToCoordinate 的 media 坐标，零换算直画
    // （fancy-canvas 对 context 预 scale(pixelRatio)，mediaSize 亦为 media 值——DPI 无关）
    target.useMediaCoordinateSpace((scope) => {
      const ctx = scope.context;
      const x1 = this.data!.x1;
      const x2 = this.data!.x2 !== null ? this.data!.x2 : scope.mediaSize.width;
      ctx.fillStyle = this.data!.color;
      ctx.fillRect(x1, 0, Math.max(x2 - x1, 1), scope.mediaSize.height);
    });
  }
}

class PositionBandView implements IPrimitivePaneView {
  rendererData: BandCoords | null = null;

  renderer(): IPrimitivePaneRenderer | null {
    return new PositionBandRenderer(this.rendererData);
  }

  update(band: PositionBandData, chart: IChartApi): void {
    const ts = chart.timeScale();
    const x1 = ts.timeToCoordinate(band.entryTime);
    const x2 = band.exitTime !== undefined ? ts.timeToCoordinate(band.exitTime) : null;
    if (x1 === null) {
      this.rendererData = null;
      return;
    }
    this.rendererData = { x1, x2, color: BAND_COLOR[band.side] };
  }
}

export class PositionBand implements ISeriesPrimitive<Time> {
  private readonly view = new PositionBandView();
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private band: PositionBandData | null = null;

  setData(band: PositionBandData | null): void {
    this.band = band;
    this.requestUpdate?.();
  }

  attached(param: { chart: IChartApi; requestUpdate: () => void }): void {
    this.chart = param.chart;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.requestUpdate = null;
  }

  updateAllViews(): void {
    if (this.band && this.chart) {
      this.view.update(this.band, this.chart);
    }
  }

  paneViews(): IPrimitivePaneView[] {
    return [this.view];
  }
}

/** 挂载 helper：attachPrimitive + 返回控制器（setData 更新带区间） */
export function attachPositionBand(series: ISeriesApi<"Candlestick", Time>): {
  primitive: PositionBand;
  setData: (band: PositionBandData | null) => void;
} {
  const primitive = new PositionBand();
  series.attachPrimitive(primitive);
  return { primitive, setData: (b) => primitive.setData(b) };
}

/** 纯函数层：持仓记录 → 带数据（单笔持仓区间映射；多笔由消费方逐笔挂载） */
export function toBandData(entry: { bar_time: string; side: "long" | "short" }, exit?: { bar_time: string }): PositionBandData {
  return {
    entryTime: toTime(entry.bar_time),
    exitTime: exit !== undefined ? toTime(exit.bar_time) : undefined,
    side: entry.side,
  };
}
