import type { Time } from "lightweight-charts";

/**
 * G-标注原语 · 信号标记族（kline-common-layer 组 4）。
 * 载体=官方 createSeriesMarkers primitive 插件通道；本模块承载纯函数映射层
 * （fills→signals→markers 两级映射），从 TradingChart 迁入作为 C4/C5 共享资产。
 */

/** 白名单操作四值（母案定案：{open,close,add,reduce} 为唯一操作定义） */
export type SignalAction = "open" | "close" | "add" | "reduce";
export type SignalSide = "long" | "short";

export interface SignalInput {
  type: string; // 兼容 Lab 旧词 buy/sell
  side?: string | null;
  timestamp: string;
}

export interface SignalMarker {
  time: Time;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
}

/** 信号样式表（KlineChart SIGNAL_STYLE 的纯函数层权威源） */
export const SIGNAL_STYLE: Record<string, Omit<SignalMarker, "time">> = {
  buy: { position: "belowBar", color: "#00C853", shape: "arrowUp", text: "B" },
  sell: { position: "aboveBar", color: "#FF1744", shape: "arrowDown", text: "S" },
  open_long: { position: "belowBar", color: "#00C853", shape: "arrowUp", text: "OL" },
  close_long: { position: "aboveBar", color: "#FF9800", shape: "arrowDown", text: "CL" },
  add_long: { position: "belowBar", color: "#00C853", shape: "circle", text: "AL" },
  reduce_long: { position: "aboveBar", color: "#FF9800", shape: "circle", text: "RL" },
  open_short: { position: "aboveBar", color: "#FF1744", shape: "arrowDown", text: "OS" },
  close_short: { position: "belowBar", color: "#7C4DFF", shape: "arrowUp", text: "CS" },
  add_short: { position: "aboveBar", color: "#FF1744", shape: "square", text: "AS" },
  reduce_short: { position: "belowBar", color: "#7C4DFF", shape: "square", text: "RS" },
};

/** 出场五 kind 归并为 close（信号动作词汇四值——与纸盘 fills 域的桥） */
const EXIT_KINDS = new Set(["exit_sl", "exit_tp", "exit_max_hold", "exit_time", "exit_liquidation"]);

export function fillAction(kind: string): SignalAction {
  return EXIT_KINDS.has(kind) ? "close" : (kind as SignalAction);
}

/** fills（纸盘成交）→ signals：出场全平类 side=null 不画（旧口径） */
export function fillsToSignals(
  fills: Array<{ kind: string; side: string | null; bar_time: string }>,
): Array<{ type: string; timestamp: string }> {
  return fills
    .filter((f) => f.side)
    .map((f) => ({
      type: `${fillAction(f.kind)}_${f.side}`,
      timestamp: f.bar_time,
    }));
}

/** signals → markers（attachPrimitive 通道前的纯映射） */
export function signalsToMarkers(signals: SignalInput[], toTime: (ts: string) => Time): SignalMarker[] {
  return signals.map((s) => {
    const style = SIGNAL_STYLE[s.type] ?? SIGNAL_STYLE.buy;
    return { time: toTime(s.timestamp), ...style };
  });
}

/** 触发点（▲）markers：决策信号列与图上 ▲ 标识对应（07-B4 契约） */
export interface TriggerInput {
  time: string;
  direction: "up" | "down";
}

export function triggersToMarkers(triggers: TriggerInput[], toTime: (ts: string) => Time): SignalMarker[] {
  return triggers.map((t) => ({
    time: toTime(t.time),
    position: t.direction === "up" ? "belowBar" : "aboveBar",
    color: t.direction === "up" ? "#26a69a" : "#ef5350",
    shape: "arrowUp",
    text: "▲",
  }));
}
