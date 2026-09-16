/**
 * Tests for PageTransition: prefers-reduced-motion 降级抽样。
 * useReducedMotion 覆写为可控返回值，motion 组件保留真实实现。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  return { ...actual, useReducedMotion: vi.fn() };
});

import { useReducedMotion } from "motion/react";
import { PageTransition } from "@/components/PageTransition";

const reducedMock = vi.mocked(useReducedMotion);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PageTransition", () => {
  it("animates in from transparent when no reduced-motion preference", () => {
    reducedMock.mockReturnValue(false);
    const { container } = render(
      <PageTransition>
        <span>页面内容</span>
      </PageTransition>,
    );
    expect(screen.getByText("页面内容")).toBeInTheDocument();
    // motion.div 同步应用 initial 样式（opacity: 0 起点）
    expect(container.firstChild).toHaveStyle({ opacity: "0" });
  });

  it("renders final state directly when reduced motion preferred", () => {
    reducedMock.mockReturnValue(true);
    const { container } = render(
      <PageTransition>
        <span>页面内容</span>
      </PageTransition>,
    );
    expect(screen.getByText("页面内容")).toBeInTheDocument();
    // 降级：普通 div 直接呈现，无初始透明动画样式
    expect(container.firstChild).not.toHaveStyle({ opacity: "0" });
  });
});
