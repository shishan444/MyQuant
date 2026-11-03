/**
 * 主题三源映射守卫（fe-shell-theme-cutover 2.3）：
 * 读 index.css 文本解析 @theme 受控变量值 vs import tokens 断言全映射；
 * antdThemeConfig 槽位同理 import 断言。同名异值即违规——T1 收口后此守卫
 * 是「从源不漂移」的机器判据（变异义务：改任一从源值守卫必红）。
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  BG,
  BORDER,
  TEXT,
  POLARITY,
  STATE,
  ATTENTION,
} from "@/theme/tokens";
import { antdThemeConfig } from "@/theme/antdTheme";

const css = readFileSync(
  join(__dirname, "../../index.css"),
  "utf-8",
);

function themeVar(name: string): string | undefined {
  const m = css.match(new RegExp(`--${name}:\\s*([^;]+);`));
  return m?.[1]?.trim();
}

describe("@theme 受控从源映射守卫（十组+两补档）", () => {
  const mapping: Array<[string, string]> = [
    ["color-text-primary", TEXT.primary],
    ["color-text-secondary", TEXT.secondary],
    ["color-text-muted", TEXT.tertiary],
    ["color-border-default", BORDER.default],
    ["color-border-faint", BORDER.faint],
    ["color-accent-gold", ATTENTION.gold],
    ["color-attention-gold", ATTENTION.gold],
    ["color-profit", POLARITY.up],
    ["color-loss", POLARITY.down],
    ["color-bg-base", BG.bg0],
    ["color-bg-surface", BG.bg1],
    ["color-bg-elevated", BG.bg2],
    ["color-warning", STATE.warn],
  ];

  it.each(mapping)("@theme --%s === tokens 值 %s", (name, expected) => {
    expect(themeVar(name)).toBe(expected);
  });

  it("body 背景为 tokens bg0 纯色（旧近黑渐变退役）", () => {
    expect(css).toContain(`background: ${BG.bg0}`);
    expect(css).not.toContain("linear-gradient(180deg, #0a0a0f");
  });
});

describe("antdTheme 槽位映射守卫", () => {
  it("背景/文本/语义槽位与 tokens 同值（bg1/bg2 预混合实色除外——公式注释在位）", () => {
    const tk = antdThemeConfig.token!;
    expect(tk.colorBgBase).toBe(BG.bg0);
    expect(tk.colorText).toBe(TEXT.primary);
    expect(tk.colorTextSecondary).toBe(TEXT.secondary);
    expect(tk.colorTextTertiary).toBe(TEXT.tertiary);
    expect(tk.colorPrimary).toBe(ATTENTION.gold);
    expect(tk.colorSuccess).toBe(POLARITY.up);
    expect(tk.colorError).toBe(POLARITY.down);
    expect(tk.colorWarning).toBe(STATE.warn);
  });
});
