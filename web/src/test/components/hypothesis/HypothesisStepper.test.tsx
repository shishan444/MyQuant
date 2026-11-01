/**
 * 02-B1 步骤条+02-B2-4 sl_mode 四档单测（hypothesis-workbench 组 3）。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  HypothesisStepper,
  SlModeSelect,
  WORKBENCH_STEPS,
  SL_MODE_OPTIONS,
} from "@/components/hypothesis/HypothesisStepper";

describe("HypothesisStepper（02-B1 五步流水线）", () => {
  it("五步全渲染且序号正确", () => {
    render(<HypothesisStepper current={0} furthest={4} />);
    for (const name of WORKBENCH_STEPS) {
      expect(screen.getByRole("button", { name: new RegExp(name) })).toBeInTheDocument();
    }
  });

  it("当前步高亮与已完成步勾标", () => {
    render(<HypothesisStepper current={2} furthest={4} />);
    expect(screen.getByRole("button", { name: /案例/ }).className).toContain("bg-accent-gold");
    expect(screen.getAllByText("✓").length).toBe(2);  // 构建+预检已完成
  });

  it("未达步禁用（furthest 限制）", () => {
    render(<HypothesisStepper current={0} furthest={1} />);
    expect((screen.getByRole("button", { name: /沙盒/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("SlModeSelect（02-B2-4 四档单源）", () => {
  it("四档=SL_MODES 全集（pct/atr/trail/series——dna.py 唯一操作定义）", () => {
    expect(SL_MODE_OPTIONS.map((o) => o.value)).toEqual(["pct", "atr", "trail", "series"]);
  });

  it("选中态 aria-checked+series 档提示", () => {
    render(<SlModeSelect value="series" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "series" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/sl_series_source/)).toBeInTheDocument();
  });
});
