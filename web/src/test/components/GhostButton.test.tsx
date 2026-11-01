/**
 * G-3 GhostButton 单测（global-nav-shell 任务 3.4）：
 * 描边次级形制（border-strong 描边+画布 text-secondary）+AntD Button 基座。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GhostButton } from "@/components/GhostButton";
import { BORDER, TEXT } from "@/theme/tokens";

describe("GhostButton（G-3）", () => {
  it("渲染描边次级形制（无填充+border-strong+画布次级文本色）", () => {
    render(<GhostButton>次要动作</GhostButton>);
    const btn = screen.getByRole("button", { name: "次要动作" });
    expect(btn).toHaveStyle({
      border: `1px solid ${BORDER.strong}`,
      color: TEXT.secondary,
    });
  });

  it("透传 AntD ButtonProps（onClick 等交互基座不重建）", () => {
    render(<GhostButton disabled>禁用</GhostButton>);
    expect(screen.getByRole("button", { name: "禁用" })).toBeDisabled();
  });
});
