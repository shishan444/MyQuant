/**
 * SSE 流式封装 L2——契约事件族（progress/complete/error）分发与流边界行为。
 * verify/batch 两 service 共用本实现，等价性由此套用例共同覆盖。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { streamSse, type StreamHandlers } from "@/realtime/sse";

function sseResponse(chunks: string[], init?: ResponseInit): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, ...init });
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

const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe("streamSse", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("progress/complete 帧按事件名分发（跨 chunk 边界拼接）", async () => {
    vi.mocked(fetch).mockResolvedValue(
      sseResponse([
        frame("progress", { current: 1, total: 3, group: "g", range_start: "a", range_end: "b", batch_results: [] }),
        "event: comp", // 故意切断帧
        "lete\ndata: {\"ok\":true}\n\n",
      ]),
    );
    const h = handlers();
    streamSse("/x", {}, h);
    await vi.waitFor(() => expect(h.calls.complete).toHaveLength(1));
    expect(h.calls.progress).toHaveLength(1);
    expect(h.calls.progress[0]).toMatchObject({ current: 1, total: 3, group: "g" });
    expect(h.calls.complete[0]).toEqual({ ok: true });
    expect(h.calls.error).toEqual([]);
  });

  it("流结束时残留 buffer 的末帧不丢失（无尾随空行）", async () => {
    vi.mocked(fetch).mockResolvedValue(
      sseResponse([frame("progress", { current: 2, total: 2, group: "g", range_start: "a", range_end: "b", batch_results: [] }).replace(/\n\n$/, "")]),
    );
    const h = handlers();
    streamSse("/x", {}, h);
    await vi.waitFor(() => expect(h.calls.progress).toHaveLength(1));
  });

  it("error 事件的 message 透传 onError；非 ok 响应走错误通道", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(sseResponse([frame("error", { message: "boom" })]))
      .mockResolvedValueOnce(new Response("server down", { status: 500 }));
    const h1 = handlers();
    streamSse("/x", {}, h1);
    await vi.waitFor(() => expect(h1.calls.error).toEqual(["boom"]));
    const h2 = handlers();
    streamSse("/x", {}, h2);
    await vi.waitFor(() => expect(h2.calls.error).toEqual(["server down"]));
  });

  it("契约外事件丢弃留痕、畸形 JSON 帧不中断流", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.mocked(fetch).mockResolvedValue(
      sseResponse(["event: mystery\ndata: {}\n\n", "event: progress\ndata: {bad json}\n\n", frame("complete", { ok: true })]),
    );
    const h = handlers();
    streamSse("/x", {}, h);
    await vi.waitFor(() => expect(h.calls.complete).toHaveLength(1));
    expect(h.calls.progress).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });

  it("abort 静默（onError 不触发）", async () => {
    const controller = new AbortController();
    const abortErr = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.mocked(fetch).mockRejectedValue(abortErr);
    const h = handlers();
    streamSse("/x", {}, h, controller.signal);
    await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(h.calls.error).toEqual([]);
  });
});
