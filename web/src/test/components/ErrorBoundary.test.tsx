/**
 * Tests for ErrorBoundary component.
 * Covers: error catching, retry mechanism, custom fallback, child remount.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorBoundary } from "@/components/ErrorBoundary";

// 渲染即抛错的测试组件
function ThrowError({ error }: { error: string }): never {
  throw new Error(error);
}

describe("ErrorBoundary", () => {
  // 屏蔽 React error boundary 的 console.error
  const originalConsoleError = console.error;
  beforeEach(() => {
    console.error = vi.fn();
  });
  afterEach(() => {
    console.error = originalConsoleError;
  });

  // --- Normal rendering ---
  it("renders children when no error", () => {
    render(
      <ErrorBoundary>
        <div data-testid="child">Hello</div>
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  // --- Error catching ---
  it("catches rendering errors and shows fallback UI", () => {
    render(
      <ErrorBoundary>
        <ThrowError error="Test error message" />
      </ErrorBoundary>,
    );
    expect(screen.getByText("Something went wrong rendering this page.")).toBeInTheDocument();
    expect(screen.getByText("Test error message")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  // --- Retry button ---
  it("resets error state on retry click", () => {
    let shouldThrow = true;

    function ConditionalThrow() {
      if (shouldThrow) throw new Error("Conditional error");
      return <div data-testid="recovered">Recovered</div>;
    }

    render(
      <ErrorBoundary>
        <ConditionalThrow />
      </ErrorBoundary>,
    );

    // Error state
    expect(screen.getByText("Something went wrong rendering this page.")).toBeInTheDocument();

    // 修复错误源后点击重试
    shouldThrow = false;
    fireEvent.click(screen.getByText("Retry"));

    // 此时应显示恢复后的内容
    expect(screen.getByTestId("recovered")).toBeInTheDocument();
  });

  // --- Custom fallback ---
  it("renders custom fallback when provided", () => {
    render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">Custom error</div>}>
        <ThrowError error="Any error" />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("custom-fallback")).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
  });

  // --- retryKey forces remount ---
  it("forces child remount via key change", () => {
    let renderCount = 0;

    function CountRenders() {
      renderCount++;
      return <div data-testid="counter">{renderCount}</div>;
    }

    const { rerender } = render(
      <ErrorBoundary>
        <CountRenders />
      </ErrorBoundary>,
    );

    expect(renderCount).toBe(1);

    // Force a re-render
    rerender(
      <ErrorBoundary>
        <CountRenders />
      </ErrorBoundary>,
    );

    expect(renderCount).toBe(2);
  });
});
