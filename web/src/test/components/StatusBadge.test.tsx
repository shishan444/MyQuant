/**
 * G-1 StatusBadge 单测（L1 契约单元层，global-nav-shell 任务 3.4）：
 * 八态描边形制（it.each 穷举）+live 唯一实心断言+尺寸两档。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/StatusBadge";
import { STATE, LIVE_BADGE } from "@/theme/tokens";

describe("StatusBadge（G-1）", () => {
  it.each(Object.keys(STATE) as Array<keyof typeof STATE>)(
    "%s 渲染描边形制（前景=状态色，边框简写含 style 可绘制）",
    (status) => {
      render(<StatusBadge status={status} />);
      const badge = screen.getByText(status);
      expect(badge).toBeInTheDocument();
      expect(badge).toHaveStyle({ color: STATE[status], borderColor: STATE[status] });
      // CARD-01：断言 border 简写面——UA 默认 border-style:none 下仅设
      // borderColor/borderWidth 浏览器不绘制；borderStyle 为空即缺陷态
      expect(badge.style.borderStyle).toBe("solid");
      expect(badge.style.borderWidth).toBe("1px");
      // 描边形制：#FFFFFF0F 染层底（jsdom 规范化为 rgba）
      expect(badge.style.backgroundColor).toBe("rgba(255, 255, 255, 0.06)");
    },
  );

  it("live 为唯一实心：live-badge-bg 底+live-badge-text 字，无状态点", () => {
    render(<StatusBadge status="live" />);
    const badge = screen.getByText("live");
    expect(badge).toHaveStyle({
      backgroundColor: LIVE_BADGE.bg,
      color: LIVE_BADGE.text,
    });
    expect(badge.querySelector("span.rounded-full")).toBeNull();
  });

  it("非 live 态含状态点且与状态色同源", () => {
    render(<StatusBadge status="running" />);
    const dot = screen.getByText("running").querySelector("span.rounded-full");
    expect(dot).not.toBeNull();
    expect(dot).toHaveStyle({ backgroundColor: STATE.running });
  });

  it("label 覆写与尺寸两档", () => {
    render(<StatusBadge status="ok" label="达标" size="md" />);
    const badge = screen.getByText("达标");
    expect(badge.className).toContain("text-xs");
    // sm 档（默认）与 className 透传
    render(<StatusBadge status="warn" className="extra-cls" />);
    const smBadge = screen.getByText("warn");
    expect(smBadge.className).toContain("text-[11px]");
    expect(smBadge.className).toContain("extra-cls");
  });
});
