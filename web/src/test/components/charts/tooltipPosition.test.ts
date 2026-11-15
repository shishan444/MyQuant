/**
 * computeTooltipPosition 纯函数直测（fix-chart-tooltip-restore 组 1）。
 *
 * 七用例域（tester §2.3 P1~P6+P1b）：上方空间足不翻转/边界值含等号
 * 仍判 top/上方不足翻 bottom/偏左右钳/偏右右钳/窄容器退化贴右/两缺省
 * 值生效。设计卡 3 契约第 4 条两难场景（浮层高过容器）不设用例。
 * 预期值全部硬编码手算，不用实现生成期望。
 */
import { describe, it, expect } from "vitest";
import {
  computeTooltipPosition,
  type TooltipPositionInput,
} from "@/components/charts/core/tooltipPosition";

const base: TooltipPositionInput = {
  anchor: { x: 200, y: 100 },
  tooltip: { width: 100, height: 30 },
  container: { width: 400, height: 200 },
};

describe("computeTooltipPosition 定位算法", () => {
  it("P1 上方空间足：不翻转，top = anchor.y - gap - height", () => {
    // 100 - 8 - 30 = 62 >= 0 → top 62；left 居中 200-50=150（域内不钳）
    expect(computeTooltipPosition(base)).toEqual({
      left: 150,
      top: 62,
      placement: "top",
    });
  });

  it("P1b 边界值：anchor.y - gap - height === 0 仍判 top（>= 含等号）", () => {
    // 38 - 8 - 30 = 0 → 恰好容纳即置顶
    expect(
      computeTooltipPosition({ ...base, anchor: { x: 200, y: 38 } }),
    ).toEqual({ left: 150, top: 0, placement: "top" });
  });

  it("P2 上方不足：翻 bottom，top = anchor.y + gap", () => {
    // 30 - 8 - 30 = -8 < 0 → bottom；30 + 8 = 38
    expect(
      computeTooltipPosition({ ...base, anchor: { x: 200, y: 30 } }),
    ).toEqual({ left: 150, top: 38, placement: "bottom" });
  });

  it("P3 悬停点偏左：left 左钳至 edgeMargin", () => {
    // anchor.x=10 → 10-50=-40 → max(-40, 4)=4
    expect(
      computeTooltipPosition({ ...base, anchor: { x: 10, y: 100 } }),
    ).toEqual({ left: 4, top: 62, placement: "top" });
  });

  it("P4 悬停点偏右：left 右钳至 container.width - width - edgeMargin", () => {
    // anchor.x=390 → 340；右界 400-100-4=296 → min=296
    expect(
      computeTooltipPosition({ ...base, anchor: { x: 390, y: 100 } }),
    ).toEqual({ left: 296, top: 62, placement: "top" });
  });

  it("P5 窄容器退化（浮层宽于容器）：取右界贴右", () => {
    // container.width=100, width=150 → 右界 100-150-4=-54；
    // 居中项 max(200-75, 4)=125 → min(125, -54) = -54（右界更小取右界=贴右）；
    // left+width=96=100-4（右内边距恒成立）
    const r = computeTooltipPosition({
      ...base,
      tooltip: { width: 150, height: 30 },
      container: { width: 100, height: 200 },
    });
    expect(r.left).toBe(-54);
    expect(r.left + 150).toBe(100 - 4);
    expect(r.placement).toBe("top");
  });

  it("P6 两缺省值生效：gap=8 与 edgeMargin=4", () => {
    // 不传 gap/edgeMargin：top 按 8 算（100-8-30=62），left 钳位按 4 算
    // （anchor.x=0 → max(-50,4)=4）——若缺省值不同（如 gap=10/margin=8）则红
    expect(
      computeTooltipPosition({
        anchor: { x: 0, y: 100 },
        tooltip: { width: 100, height: 30 },
        container: { width: 400, height: 200 },
      }),
    ).toEqual({ left: 4, top: 62, placement: "top" });
  });
});
