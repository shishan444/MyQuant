/**
 * Tests for useEvolution hooks: query key factory, mutations, WebSocket invalidation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock API services
vi.mock("@/services/evolution", () => ({
  getEvolutionTasks: vi.fn(),
  getEvolutionTask: vi.fn(),
  getEvolutionHistory: vi.fn(),
  getDiscoveredStrategies: vi.fn(),
  getAllDiscoveredStrategies: vi.fn(),
  createEvolutionTask: vi.fn(),
  stopEvolutionTask: vi.fn(),
  pauseEvolutionTask: vi.fn(),
  resumeEvolutionTask: vi.fn(),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock WebSocket
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  readyState = 1;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() { this.readyState = 3; }
  send() {}

  static getLast(): MockWebSocket | undefined {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  static reset() {
    MockWebSocket.instances = [];
  }
}

// @ts-expect-error mock
globalThis.WebSocket = MockWebSocket;

import {
  evolutionKeys,
  useEvolutionTasks,
  useEvolutionTask,
  useCreateEvolutionTask,
  useStopEvolutionTask,
  usePauseEvolutionTask,
  useResumeEvolutionTask,
  useEvolutionWebSocket,
} from "@/hooks/useEvolution";
import {
  createEvolutionTask,
  stopEvolutionTask,
  pauseEvolutionTask,
  resumeEvolutionTask,
} from "@/services/evolution";
import { toast } from "sonner";

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        {children}
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  MockWebSocket.reset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("evolutionKeys", () => {
  it("generates correct key structures", () => {
    expect(evolutionKeys.all).toEqual(["evolution"]);
    expect(evolutionKeys.tasks()).toEqual(["evolution", "tasks", undefined]);
    expect(evolutionKeys.tasks({ status: "running" })).toEqual(["evolution", "tasks", { status: "running" }]);
    expect(evolutionKeys.task("abc")).toEqual(["evolution", "task", "abc"]);
    expect(evolutionKeys.history("abc")).toEqual(["evolution", "history", "abc"]);
    expect(evolutionKeys.discovered("abc")).toEqual(["evolution", "discovered", "abc"]);
  });
});

describe("useEvolutionTasks", () => {
  it("returns query options with correct key", () => {
    const result = useEvolutionTasks({ status: "running" });
    expect(result.queryKey).toEqual(["evolution", "tasks", { status: "running" }]);
  });
});

describe("useEvolutionTask", () => {
  it("returns query options disabled for empty id", () => {
    const result = useEvolutionTask("");
    expect(result.enabled).toBe(false);
  });

  it("returns query options enabled for valid id", () => {
    const result = useEvolutionTask("abc123");
    expect(result.enabled).toBe(true);
    expect(result.queryKey).toEqual(["evolution", "task", "abc123"]);
  });
});

describe("useCreateEvolutionTask", () => {
  it("calls API and invalidates queries on success", async () => {
    vi.mocked(createEvolutionTask).mockResolvedValueOnce({ task_id: "new1" } as any);

    const { result } = renderHook(() => useCreateEvolutionTask(), { wrapper: createWrapper() });

    result.current.mutate({ symbol: "BTCUSDT", timeframe: "4h" } as any);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(createEvolutionTask).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith("进化任务已创建");
  });
});

describe("useStopEvolutionTask", () => {
  it("calls API and shows success toast", async () => {
    vi.mocked(stopEvolutionTask).mockResolvedValueOnce({} as any);

    const { result } = renderHook(() => useStopEvolutionTask(), { wrapper: createWrapper() });

    result.current.mutate("t1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stopEvolutionTask).toHaveBeenCalledWith("t1", expect.anything());
    expect(toast.success).toHaveBeenCalledWith("任务已停止");
  });
});

describe("usePauseEvolutionTask", () => {
  it("calls API and shows success toast", async () => {
    vi.mocked(pauseEvolutionTask).mockResolvedValueOnce({} as any);

    const { result } = renderHook(() => usePauseEvolutionTask(), { wrapper: createWrapper() });

    result.current.mutate("t1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(pauseEvolutionTask).toHaveBeenCalledWith("t1", expect.anything());
    expect(toast.success).toHaveBeenCalledWith("任务已暂停", { description: "可以随时恢复" });
  });
});

describe("useResumeEvolutionTask", () => {
  it("calls API and shows success toast", async () => {
    vi.mocked(resumeEvolutionTask).mockResolvedValueOnce({} as any);

    const { result } = renderHook(() => useResumeEvolutionTask(), { wrapper: createWrapper() });

    result.current.mutate("t1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(resumeEvolutionTask).toHaveBeenCalledWith("t1", expect.anything());
    expect(toast.success).toHaveBeenCalledWith("任务已恢复");
  });
});

describe("useEvolutionWebSocket", () => {
  it("does not connect when taskId is null", () => {
    renderHook(() => useEvolutionWebSocket(null), { wrapper: createWrapper() });
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it("connects to WebSocket when taskId is provided", () => {
    renderHook(() => useEvolutionWebSocket("task1"), { wrapper: createWrapper() });
    expect(MockWebSocket.instances).toHaveLength(1);
    const ws = MockWebSocket.getLast()!;
    expect(ws.url).toContain("/ws/evolution/task1");
  });

  it("closes WebSocket on unmount", () => {
    const { unmount } = renderHook(() => useEvolutionWebSocket("task1"), { wrapper: createWrapper() });
    const ws = MockWebSocket.getLast()!;
    const closeSpy = vi.spyOn(ws, "close");

    unmount();
    expect(closeSpy).toHaveBeenCalled();
  });

  it("handles task_started message by invalidating queries", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    renderHook(() => useEvolutionWebSocket("task1"), { wrapper });

    const ws = MockWebSocket.getLast()!;
    act(() => {
      ws.onmessage!({ data: JSON.stringify({ type: "task_started" }) });
    });

    expect(invalidateSpy).toHaveBeenCalled();
  });

  it("handles generation_complete message with cache update", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    // Pre-populate cache
    qc.setQueryData(evolutionKeys.task("task1"), {
      task_id: "task1",
      current_generation: 5,
      best_score: 60,
      best_fitness: 0.55,
      qualified_count: 2,
    });
    qc.setQueryData(evolutionKeys.history("task1"), {
      records: [{ generation: 5, best_score: 60, avg_score: 40, best_fitness: 0.55, avg_fitness: 0.38, created_at: "2025-01-01" }],
    });

    renderHook(() => useEvolutionWebSocket("task1"), { wrapper });

    const ws = MockWebSocket.getLast()!;
    act(() => {
      ws.onmessage!({
        data: JSON.stringify({
          type: "generation_complete",
          generation: 6,
          best_score: 72,
          avg_score: 45,
          best_fitness: 0.68,
          qualified_count: 3,
        }),
      });
    });

    // Task cache should be updated
    const taskData = qc.getQueryData(evolutionKeys.task("task1")) as Record<string, unknown>;
    expect(taskData.current_generation).toBe(6);
    expect(taskData.best_score).toBe(72);

    // History should have new record
    const historyData = qc.getQueryData(evolutionKeys.history("task1")) as { records: unknown[] };
    expect(historyData.records).toHaveLength(2);
  });

  it("handles evolution_complete message", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    qc.setQueryData(evolutionKeys.task("task1"), {
      task_id: "task1",
      status: "running",
    });

    renderHook(() => useEvolutionWebSocket("task1"), { wrapper });

    const ws = MockWebSocket.getLast()!;
    act(() => {
      ws.onmessage!({
        data: JSON.stringify({
          type: "evolution_complete",
          best_score: 85,
          best_fitness: 0.82,
          champion_dna: '{"signal_genes":[]}',
        }),
      });
    });

    const taskData = qc.getQueryData(evolutionKeys.task("task1")) as Record<string, unknown>;
    expect(taskData.status).toBe("completed");
    expect(taskData.best_score).toBe(85);
  });
});
