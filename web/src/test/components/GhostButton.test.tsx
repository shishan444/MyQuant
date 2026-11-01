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
    // CARD-03：画布 G-3 Label fs12+cornerRadius 4（经 RADIUS.badge 权威源）
    // 内联直断言：toHaveStyle 走 getComputedStyle 受 AntD 注入类干扰（探针实证内联值齐全）
    expect(btn.style.fontSize).toBe("12px");
    expect(btn.style.borderRadius).toBe("4px");
  });

  it("透传 AntD ButtonProps（onClick 等交互基座不重建）", () => {
    render(<GhostButton disabled>禁用</GhostButton>);
    expect(screen.getByRole("button", { name: "禁用" })).toBeDisabled();
  });

  it("调用方 style 后置覆写默认形制（合并语义：自定义优先）", () => {
    render(<GhostButton style={{ border: "2px dashed red" }}>覆写</GhostButton>);
    // 内联样式属性直断言：toHaveStyle 的 border shorthand 解析在 jsdom 不可靠
    const btn = screen.getByRole("button", { name: "覆写" }) as HTMLElement;
    expect(btn.style.border).toBe("2px dashed red");
    // 未覆写项保留默认（padding）
    expect(btn.style.padding).toBe("4px 10px");
  });
});
