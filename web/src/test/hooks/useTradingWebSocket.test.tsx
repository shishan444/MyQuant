/**
 * Tests for useTradingWebSocket hook.
 * Covers: connection lifecycle, message handling, reconnection, cleanup.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Mock API services
vi.mock("@/services/trading", () => ({
  listTradingTasks: vi.fn(),
  getTradingTask: vi.fn(),
  createTradingTask: vi.fn(),
  stopTradingTask: vi.fn(),
  getTradingTrades: vi.fn(),
  getTradingRunnerStatus: vi.fn(),
  getTradingEquity: vi.fn(),
  getTradingMetrics: vi.fn(),
  deleteTradingTask: vi.fn(),
}));

// Mock sonner
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock WebSocket
class MockWS {
  static instances: MockWS[] = [];
  url: string;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  readyState = 1;

  constructor(url: string) {
    this.url = url;
    MockWS.instances.push(this);
  }
  close() { this.readyState = 3; }
  send() {}

  static getLast() { return MockWS.instances[MockWS.instances.length - 1]; }
  static reset() { MockWS.instances = []; }
}

// @ts-expect-error mock
globalThis.WebSocket = MockWS;

import { useTradingWebSocket } from "@/hooks/useTrading";

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
    vi.stubEnv("VITE_REALTIME_TRANSPORT", "native");  // B8.4 后默认 socketio——本套锚定 native 实现面
  vi.clearAllMocks();
  MockWS.reset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useTradingWebSocket", () => {
  it("does not connect when taskId is null", () => {
    renderHook(() => useTradingWebSocket(null), { wrapper: createWrapper() });
    expect(MockWS.instances).toHaveLength(0);
  });

  it("connects to WebSocket with correct URL", () => {
    renderHook(() => useTradingWebSocket("t1"), { wrapper: createWrapper() });
    expect(MockWS.instances).toHaveLength(1);
    expect(MockWS.getLast()!.url).toContain("/ws/trading/t1");
  });

  it("returns false initially and true after onopen", () => {
    const { result } = renderHook(() => useTradingWebSocket("t1"), { wrapper: createWrapper() });
    expect(result.current).toBe(false);

    const ws = MockWS.getLast()!;
    act(() => { ws.onopen!(); });

    expect(result.current).toBe(true);
  });

  it("handles position_update message by scheduling invalidation", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    renderHook(() => useTradingWebSocket("t1"), { wrapper });

    const ws = MockWS.getLast()!;
    act(() => {
      ws.onmessage!({ data: JSON.stringify({ type: "position_update", task_id: "t1", position: null, balance: 100000, equity: 100000, unrealized_pnl: 0, total_trades: 0, total_pnl: 0 }) });
    });

    // Invalidation is delayed 2000ms
    act(() => { vi.advanceTimersByTime(2000); });
    expect(invalidateSpy).toHaveBeenCalled();
  });

  it("handles task_started message by scheduling invalidation", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    renderHook(() => useTradingWebSocket("t1"), { wrapper });

    const ws = MockWS.getLast()!;
    act(() => {
      ws.onmessage!({ data: JSON.stringify({ type: "task_started", task_id: "t1" }) });
    });

    act(() => { vi.advanceTimersByTime(2000); });
    expect(invalidateSpy).toHaveBeenCalled();
  });

  it("ignores unknown message types", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    renderHook(() => useTradingWebSocket("t1"), { wrapper });

    const ws = MockWS.getLast()!;
    act(() => {
      ws.onmessage!({ data: JSON.stringify({ type: "unknown_type" }) });
    });

    act(() => { vi.advanceTimersByTime(2000); });
    // Should not schedule invalidation for unknown types
    // (the scheduleInvalidation in onmessage only fires for position_update/task_started)
    // But since there's a catch-all scheduleInvalidation outside the if... let me check
    // Actually looking at the code, only position_update and task_started trigger it
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("ignores invalid JSON messages", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    renderHook(() => useTradingWebSocket("t1"), { wrapper });

    const ws = MockWS.getLast()!;
    // Should not throw
    act(() => {
      ws.onmessage!({ data: "not valid json" });
    });
  });

  it("sets isConnected to false on close", () => {
    const { result } = renderHook(() => useTradingWebSocket("t1"), { wrapper: createWrapper() });

    const ws = MockWS.getLast()!;
    act(() => { ws.onopen!(); });
    expect(result.current).toBe(true);

    act(() => { ws.onclose!(); });
    expect(result.current).toBe(false);
  });

  it("closes WebSocket on unmount", () => {
    const { unmount } = renderHook(() => useTradingWebSocket("t1"), { wrapper: createWrapper() });
    const ws = MockWS.getLast()!;
    const closeSpy = vi.spyOn(ws, "close");

    unmount();
    expect(closeSpy).toHaveBeenCalled();
  });
});
