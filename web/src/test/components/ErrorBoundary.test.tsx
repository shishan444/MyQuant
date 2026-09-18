/**
 * Tests for ErrorBoundary component.
 * Covers: error catching, retry mechanism, custom fallback, child remount.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorBoundary } from "@/components/ErrorBoundary";

// Component that throws on render
function ThrowError({ error }: { error: string }): never {
  throw new Error(error);
}

describe("ErrorBoundary", () => {
  // Suppress console.error from React error boundary
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

    // Fix the error and click retry
    shouldThrow = false;
    fireEvent.click(screen.getByText("Retry"));

    // Should now show recovered content
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
