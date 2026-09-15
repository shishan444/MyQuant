/**
 * SSE 流式通道统一封装——契约见 ./contract.md（流式通道事件族）。
 *
 * 现状实现：POST + fetch/SSE（verify/batch 两端点现状形态）。
 * 目标态（后端容器 B8 批）：同语义事件改经 socket.io emit 下发，
 * 本模块消费侧接口不变，仅替换内部实现。
 */
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

/**
 * 消费单条 SSE 帧（event:/data: 两行），按事件名分发。
 * 定义在读取循环外：流结束时残留 buffer 的事件也能被处理。
 */
function processSsePart<TComplete>(
  part: string,
  handlers: StreamHandlers<TComplete>,
): void {
  if (!part.trim()) return;
  const lines = part.split("\n");
  const eventType = lines.find((l) => l.startsWith("event: "))?.slice(7);
  const dataRaw = lines.find((l) => l.startsWith("data: "))?.slice(6);
  if (!eventType || !dataRaw) return;
  try {
    const data: unknown = JSON.parse(dataRaw);
    if (eventType === "progress") handlers.onProgress(data as StreamProgressEvent);
    else if (eventType === "complete") handlers.onComplete(data as TComplete);
    else if (eventType === "error") {
      const message =
        typeof data === "object" && data !== null && "message" in data
          ? String((data as { message: unknown }).message)
          : "Unknown error";
      handlers.onError(message);
    } else {
      console.warn(`[realtime] SSE: 契约外事件已丢弃（event=${eventType}）`);
    }
  } catch {
    console.warn(`[realtime] SSE: 帧解析失败已丢弃（event=${eventType}）`, dataRaw);
  }
}

export function streamSse<TComplete>(
  url: string,
  payload: unknown,
  handlers: StreamHandlers<TComplete>,
  signal?: AbortSignal,
): void {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || "";
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
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop()!;

        for (const part of parts) processSsePart(part, handlers);
      }

      // 流结束：残留 buffer 的事件同样处理
      if (buffer.trim()) processSsePart(buffer, handlers);
    })
    .catch((err) => {
      if (err.name !== "AbortError") handlers.onError(err.message);
    });
}
