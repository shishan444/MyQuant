/**
 * 流式通道统一封装——契约见 ./contract.md（流式通道事件族）。
 *
 * B8.3 双端批：POST 返回 {session_id}，进度经 socket.io /streams
 * namespace（room=session_id）emit 下发——事件名 progress/complete/error
 * 与 SSE 帧同名，zod schema 复用。消费侧接口（streamSse 签名）不变。
 * 取消（AbortSignal）→ C→S cancel 事件协作取消（B8.0 语义点①）。
 */
import { io, type Socket } from "socket.io-client";

/** 流式 progress 事件（契约：contract.md 流式通道） */
export interface StreamProgressEvent {
  current: number;
  total: number;
  group: string;
  range_start: string;
  range_end: string;
  batch_results: Array<Record<string, unknown>>;
}

export interface StreamHandlers<TComplete> {
  onProgress: (event: StreamProgressEvent) => void;
  onComplete: (data: TComplete) => void;
  onError: (message: string) => void;
}

export function streamSse<TComplete>(
  url: string,
  payload: unknown,
  handlers: StreamHandlers<TComplete>,
  signal?: AbortSignal,
): void {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || "";

  const subscribe = (sessionId: string): void => {
    const socket: Socket = io("/streams", {
      transports: ["websocket"],
      query: { session_id: sessionId },
    });
    const teardown = (): void => {
      socket.removeAllListeners();
      socket.disconnect();
    };
    socket.on("progress", (d: unknown) => handlers.onProgress(d as StreamProgressEvent));
    socket.on("complete", (d: unknown) => {
      handlers.onComplete(d as TComplete);
      teardown();
    });
    socket.on("error", (d: unknown) => {
      const message =
        typeof d === "object" && d !== null && "message" in d
          ? String((d as { message: unknown }).message)
          : "Unknown error";
      handlers.onError(message);
      teardown();
    });
    signal?.addEventListener("abort", () => {
      // 协作取消（服务端步粒度取消后回发 error 事件收尾）
      socket.emit("cancel", { session_id: sessionId });
      teardown();
    }, { once: true });
  };

  fetch(`${baseUrl}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        handlers.onError(text || `HTTP ${response.status}`);
        return;
      }
      const data = (await response.json()) as { session_id?: string };
      if (!data.session_id) {
        handlers.onError("Missing session_id in stream response");
        return;
      }
      subscribe(data.session_id);
    })
    .catch((err) => {
      if (err.name !== "AbortError") handlers.onError(err.message);
    });
}
