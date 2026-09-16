/**
 * 连接适配层 L2——双实现（原生 WS / socket.io-client）同套接口测试：
 * 接口形状（onMessage/onStatus/close）、协议消息消化、开关选择。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// socket.io-client mock（实现二）——工厂内建（vi.mock 提升约束），经 vi.mocked 取用
// ---------------------------------------------------------------------------

vi.mock("socket.io-client", () => ({
  io: vi.fn(() => ({ onAny: vi.fn(), on: vi.fn(), disconnect: vi.fn() })),
}));

import { io } from "socket.io-client";
import {
  createRealtimeConnection,
  type RealtimeConnection,
} from "@/realtime/connection";

const ioMock = vi.mocked(io);
/** 取工厂最新产出的 socket mock */
const lastSocket = () => ioMock.mock.results[ioMock.mock.results.length - 1].value as {
  onAny: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
};

// ---------------------------------------------------------------------------
// 原生 WebSocket mock（实现一）
// ---------------------------------------------------------------------------

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  readyState = MockWebSocket.OPEN;
  sent: string[] = [];
  onmessage: ((ev: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.();
  }
  // 测试辅助
  emitServer(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
  static getLast() {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }
}

function withMockWs<T>(fn: () => T): T {
  const original = globalThis.WebSocket;
  globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  try {
    return fn();
  } finally {
    globalThis.WebSocket = original;
  }
}

const opts = { channel: "evolution" as const, taskId: "t1" };

describe("实现一 · 原生 WS 连接", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_REALTIME_TRANSPORT", "native");
  });
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it("连接到契约 URL（/ws/{channel}/{taskId}）", () => {
    withMockWs(() => {
      createRealtimeConnection(opts);
      expect(MockWebSocket.getLast().url).toContain("/ws/evolution/t1");
    });
  });

  it("业务消息透传 onMessage；协议消息（subscribed/pong）不上抛", () => {
    withMockWs(() => {
      const conn = createRealtimeConnection(opts);
      const received: unknown[] = [];
      conn.onMessage((d) => received.push(d));
      const ws = MockWebSocket.getLast();

      ws.emitServer({ type: "subscribed", task_id: "t1" });
      ws.emitServer({ type: "pong" });
      ws.emitServer({ type: "phase_changed", task_id: "t1", phase: "p" });

      expect(received).toEqual([{ type: "phase_changed", task_id: "t1", phase: "p" }]);
      conn.close();
    });
  });

  it("onStatus 上报 open/close；断开后 3s 重连", () => {
    withMockWs(() => {
      const conn = createRealtimeConnection(opts);
      const statuses: boolean[] = [];
      conn.onStatus((c) => statuses.push(c));
      const ws = MockWebSocket.getLast();

      ws.onopen?.();
      ws.onclose?.();
      expect(statuses).toEqual([true, false]);

      vi.advanceTimersByTime(3000);
      expect(MockWebSocket.instances).toHaveLength(2);
      conn.close();
    });
  });

  it("close 后不再重连、心跳停止", () => {
    withMockWs(() => {
      const conn = createRealtimeConnection(opts);
      const ws = MockWebSocket.getLast();
      ws.onopen?.();
      conn.close();

      vi.advanceTimersByTime(30_000);
      expect(MockWebSocket.instances).toHaveLength(1);
      expect(ws.sent).toEqual([]); // 心跳未发出（close 前 20s 内无 tick）
    });
  });

  it("连接建立后按契约发 20s 心跳 ping", () => {
    withMockWs(() => {
      const conn = createRealtimeConnection(opts);
      const ws = MockWebSocket.getLast();
      ws.onopen?.();

      vi.advanceTimersByTime(20_000);
      expect(ws.sent).toEqual([JSON.stringify({ type: "ping" })]);
      conn.close();
    });
  });
});

describe("实现二 · socket.io 连接（就位未启用）", () => {
  beforeEach(() => {
    ioMock.mockReset();
    ioMock.mockImplementation(() =>
      ({ onAny: vi.fn(), on: vi.fn(), disconnect: vi.fn() }) as never,
    );
  });

  it("开关开启时走 socket.io 分支并连接契约 namespace", () => {
    vi.stubEnv("VITE_REALTIME_TRANSPORT", "socketio");
    const conn: RealtimeConnection = createRealtimeConnection(opts);
    expect(ioMock).toHaveBeenCalledWith("/evolution", expect.objectContaining({ query: { task_id: "t1" } }));
    conn.close();
    expect(lastSocket().disconnect).toHaveBeenCalled();
    vi.unstubAllEnvs();
  });

  it("onMessage 绑定 onAny 透传；onStatus 绑定 connect/disconnect 且语义正确", () => {
    vi.stubEnv("VITE_REALTIME_TRANSPORT", "socketio");
    const conn = createRealtimeConnection(opts);
    const statuses: boolean[] = [];
    conn.onMessage(() => {});
    conn.onStatus((c) => statuses.push(c));

    const sock = lastSocket();
    const bindings = new Map(sock.on.mock.calls.map(([ev, cb]) => [ev as string, cb as (v?: unknown) => void]));
    expect(sock.onAny).toHaveBeenCalled();
    expect(bindings.has("connect")).toBe(true);
    expect(bindings.has("disconnect")).toBe(true);

    // 接口语义：connect→true，disconnect→false
    bindings.get("connect")!();
    bindings.get("disconnect")!();
    expect(statuses).toEqual([true, false]);
    conn.close();
    vi.unstubAllEnvs();
  });

  it("默认（无开关）走 socket.io 分支（B8.4 退役批后默认反转）", () => {
    vi.unstubAllEnvs();
    const c = createRealtimeConnection({ channel: "evolution", taskId: "t1" });
    expect(ioMock).toHaveBeenCalled();
    c.close();
  });
});
