/**
 * StarRating 组件测（fe-research-execution-domain 4.1）。
 * 着色三态取色锚：lit>=4→#FFC76B（ATTENTION.gold 内联——终态新值，
 * 勿回旧值 #fbbf24）/lit>=3→text-blue-400 utility/其余→text-text-muted；
 * 槽位恒 5（MiniStars 五槽蓝本），value 容 null（→0）。
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { StarRating } from "@/components/StarRating";

function filledCount(container: HTMLElement): number {
  return Array.from(container.querySelectorAll("svg")).filter((s) =>
    s.getAttribute("class")?.includes("fill-current"),
  ).length;
}

describe("StarRating（verify_star 1-5 制星级）", () => {
  it("lit>=4：金色 #FFC76B 内联着色（ATTENTION.gold 终态取色——jsdom 序列化为 rgb 同值）", () => {
    const { container } = render(<StarRating value={5} />);
    const span = container.querySelector("span.flex") as HTMLElement;
    expect(span.style.color).toBe("rgb(255, 199, 107)"); // #FFC76B
    // 5 槽全填充
    expect(container.querySelectorAll("svg")).toHaveLength(5);
    expect(filledCount(container)).toBe(5);
  });

  it("lit=4 同金色档（>=4 口径非 ===5——两页同色收口）", () => {
    const { container } = render(<StarRating value={4} />);
    expect((container.querySelector("span.flex") as HTMLElement).style.color).toBe(
      "rgb(255, 199, 107)",
    );
  });

  it("lit=3：text-blue-400 utility（无内联色）+3 槽填充", () => {
    const { container } = render(<StarRating value={3} />);
    const span = container.querySelector("span.flex") as HTMLElement;
    expect(span.className).toContain("text-blue-400");
    expect(span.getAttribute("style")).toBeNull();
    expect(filledCount(container)).toBe(3);
  });

  it("lit<3：text-text-muted；null→0 全空槽", () => {
    const { container } = render(<StarRating value={1} />);
    expect(container.querySelector("span.flex")?.className).toContain("text-text-muted");
    const { container: c2 } = render(<StarRating value={null} />);
    const svgs = Array.from(c2.querySelectorAll("svg"));
    expect(svgs).toHaveLength(5);
    expect(svgs.every((s) => s.getAttribute("class")?.includes("opacity-30"))).toBe(true);
    expect(filledCount(c2)).toBe(0);
  });
});
