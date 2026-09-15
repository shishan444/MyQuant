/**
 * 实时连接适配层——契约见 ./contract.md。
 *
 * 消费层只面向 RealtimeConnection 接口，对底层传输（原生 WS / socket.io）无感知。
 * 协议消息（subscribed/pong/ping）在本层消化，业务 handler 只收到业务事件帧。
 *
 * 实现选择（特性开关）：
 * - 实现一 · 原生 WebSocket（当前启用，现有后端端点 /ws/{channel}/{taskId}）
 * - 实现二 · socket.io-client（就位未启用——后端容器 B8 批实现端点后，
 *   设 VITE_REALTIME_TRANSPORT=socketio 并与后端同批切换）
 */
import { io, type Socket } from "socket.io-client";
import { ProtocolMessage } from "./schemas";

type RealtimeChannel = "evolution" | "trading";

export interface RealtimeConnection {
  /** 注册业务消息处理器（协议消息已被本层消化，不会到达 handler） */
  onMessage(handler: (data: unknown) => void): void;
  /** 注册连接状态处理器（open/close/重连均上报） */
  onStatus(handler: (connected: boolean) => void): void;
  close(): void;
}

export interface RealtimeConnectionOptions {
  channel: RealtimeChannel;
  taskId: string;
}

/** 20s 心跳间隔（契约：C→S ping） */
const HEARTBEAT_MS = 20_000;

function resolveHost(): string {
  const override = import.meta.env.VITE_WS_URL as string | undefined;
  if (override) return new URL(override).host;
  return window.location.host;
}

// ---------------------------------------------------------------------------
// 实现一：原生 WebSocket（启用中）
// ---------------------------------------------------------------------------

function createNativeWsConnection({ channel, taskId }: RealtimeConnectionOptions): RealtimeConnection {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${resolveHost()}/ws/${channel}/${taskId}`;

  let handler: ((data: unknown) => void) | null = null;
  let statusHandler: ((connected: boolean) => void) | null = null;
  let closed = false;
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let ws: WebSocket | null = null;

  /** 协议消息判定单源=schemas.ts 契约（subscribed/pong） */
  const isProtocolMessage = (data: unknown): boolean => ProtocolMessage.safeParse(data).success;

  function connect() {
    if (closed) return;
    ws = new WebSocket(url);

    ws.onmessage = (event) => {
      let raw: unknown;
      try {
        raw = JSON.parse(event.data);
      } catch {
        console.warn(`[realtime] ${channel}: JSON 解析失败已丢弃`, event.data);
        return;
      }
      // 协议消息在连接层消化，业务层不可见（契约纪律 2）
      if (isProtocolMessage(raw)) return;
      handler?.(raw);
    };

    ws.onopen = () => {
      statusHandler?.(true);
      heartbeatTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, HEARTBEAT_MS);
    };

    ws.onclose = () => {
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      heartbeatTimer = null;
      statusHandler?.(false);
      if (!closed) reconnectTimer = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  connect();

  return {
    onMessage(h) {
      handler = h;
    },
    onStatus(h) {
      statusHandler = h;
    },
    close() {
      closed = true;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
      ws = null;
    },
  };
}

// ---------------------------------------------------------------------------
// 实现二：socket.io-client（就位未启用——B8 批与后端端点同批切换）
// ---------------------------------------------------------------------------

function createSocketIoConnection({ channel, taskId }: RealtimeConnectionOptions): RealtimeConnection {
  // 契约目标形态：namespace /{channel}，room=taskId 由服务端按连接参数定位
  const socket: Socket = io(`/${channel}`, {
    transports: ["websocket"],
    // 服务端尚未实现（B8），此处仅就位：不自动重试风暴
    reconnection: true,
    reconnectionDelay: 3000,
    query: { task_id: taskId },
  });

  return {
    onMessage(h) {
      socket.onAny((_event, data) => h(data));
    },
    onStatus(h) {
      socket.on("connect", () => h(true));
      socket.on("disconnect", () => h(false));
    },
    close() {
      socket.disconnect();
    },
  };
}

export function createRealtimeConnection(
  opts: RealtimeConnectionOptions,
): RealtimeConnection {
  const transport = import.meta.env.VITE_REALTIME_TRANSPORT as string | undefined;
  if (transport === "socketio") {
    return createSocketIoConnection(opts);
  }
  return createNativeWsConnection(opts);
}
