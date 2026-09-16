/**
 * 流式封装 L2（B8.3 重锚定）：POST→session_id→socket.io /streams 订阅
 * 分发（progress/complete/error 与 SSE 帧同名），AbortSignal→cancel 协作取消。
 * 消费侧契约（streamSse 签名/handlers 形制）不变——等价性由此套用例覆盖。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { Socket } from "socket.io-client";
import { streamSse, type StreamHandlers } from "@/realtime/sse";

type Emit = (event: string, data: unknown) => void;

const socketEvents = new Map<string, Emit>();
const emitted: Array<{ event: string; data: unknown }> = [];
let lastSocket: Socket;

vi.mock("socket.io-client", () => ({
  io: vi.fn((_ns: string, _opts: unknown) => {
    const listeners = new Map<string, Emit>();
    lastSocket = {
      on: (e: string, h: Emit) => listeners.set(e, h),
      emit: (e: string, d: unknown) => emitted.push({ event: e, data: d }),
      disconnect: () => {},
      removeAllListeners: () => listeners.clear(),
    } as unknown as Socket;
    (lastSocket as unknown as { _l?: Map<string, Emit> })._l = listeners;
    return lastSocket;
  }),
}));

async function waitForSubscribed(): Promise<void> {
  await vi.waitFor(() =>
    expect((lastSocket as unknown as { _l?: Map<string, unknown> })._l?.size)
      .toBeGreaterThan(0),
  );
}

function fire(event: string, data: unknown): void {
  const l = (lastSocket as unknown as { _l?: Map<string, Emit> })._l;
  l?.get(event)?.(data);
}

function jsonSession(sessionId: string): Response {
  return new Response(JSON.stringify({ session_id: sessionId }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function handlers(): StreamHandlers<{ ok: boolean }> & {
  calls: { progress: unknown[]; complete: unknown[]; error: string[] };
} {
  const calls = { progress: [], complete: [], error: [] } as {
    progress: unknown[];
    complete: unknown[];
    error: string[];
  };
  return {
    calls,
    onProgress: (e) => calls.progress.push(e),
    onComplete: (d) => calls.complete.push(d),
    onError: (m) => calls.error.push(m),
  };
}

describe("streamSse", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    emitted.length = 0;
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("POST 返回 session_id 后经 socket 事件分发 progress/complete", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonSession("s-1"));
    const h = handlers();
    streamSse("/verify/stream", {}, h);
    await waitForSubscribed();

    fire("progress", { current: 1, total: 3 });
    fire("complete", { ok: true });
    expect(h.calls.progress).toHaveLength(1);
    expect(h.calls.complete).toEqual([{ ok: true }]);
  });

  it("error 事件 message 透传 onError；非 ok 响应走错误通道", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("server down", { status: 500 }),
    );
    const h = handlers();
    streamSse("/x", {}, h);
    await vi.waitFor(() =>
      expect(h.calls.error[0]).toMatch(/server down|500/),
    );
  });

  it("AbortSignal 触发 C→S cancel 协作取消", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonSession("s-2"));
    const ctrl = new AbortController();
    const h = handlers();
    streamSse("/x", {}, h, ctrl.signal);
    await waitForSubscribed();
    ctrl.abort();
    expect(emitted).toContainEqual({
      event: "cancel",
      data: { session_id: "s-2" },
    });
  });

  it("响应缺 session_id 走错误通道", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({}), { status: 200 }),
    );
    const h = handlers();
    streamSse("/x", {}, h);
    await vi.waitFor(() => expect(h.calls.error[0]).toMatch(/session_id/));
  });
});
