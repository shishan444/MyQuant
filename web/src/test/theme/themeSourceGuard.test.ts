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

describe("@theme+:root 受控从源映射守卫（@theme 十三组+:root 声称同步面）", () => {
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
    // ── :root shadcn 块「声称同步即受守卫」通则（fe-polarity-calibration
    // spec：无「声称而不受控」的半从源）——块注释声称面 9 项全集入守卫。
    // destructive=1.2 先行；其余 8 项=T2.4 扩容（tester3）：background=BG 族/
    // foreground 三级=TEXT 族/primary=ATTENTION.gold/accent 系/border 族；
    // 零消费保留项（popover/secondary/muted 本体/ring）注释明示「保留现值
    // 归⑤收尾门议」非声称同步，不入。
    ["destructive", POLARITY.down], // fe-polarity-calibration 1.2：:root --destructive 入守卫
    ["background", BG.bg0], // T2.4：:root --background=BG.bg0
    ["foreground", TEXT.primary], // T2.4：:root --foreground=TEXT.primary
    ["muted-foreground", TEXT.tertiary], // T2.4：:root --muted-foreground=TEXT.tertiary
    ["primary", ATTENTION.gold], // T2.4：:root --primary=ATTENTION.gold
    ["accent-foreground", ATTENTION.gold], // T2.4：:root --accent-foreground=ATTENTION.gold
    ["border", BORDER.default], // T2.4：:root --border=BORDER.default
    ["input", BORDER.default], // T2.4：:root --input=BORDER.default
  ];

  it.each(mapping)("--%s === tokens 值 %s", (name, expected) => {
    expect(themeVar(name)).toBe(expected);
  });

  it("--accent === ATTENTION.gold 15% alpha（「accent 系=金 15%」声称——rgba 解析形免排版敏感）", () => {
    const m = themeVar("accent")?.match(/^rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)$/);
    expect(m, "--accent 应为 rgba(r,g,b,a) 形").toBeDefined();
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(ATTENTION.gold.slice(i, i + 2), 16));
    expect(Number(m![1])).toBe(r);
    expect(Number(m![2])).toBe(g);
    expect(Number(m![3])).toBe(b);
    expect(Number(m![4])).toBeCloseTo(0.15, 6);
  });

  it("body 背景为 tokens bg0 纯色（旧近黑渐变退役）", () => {
    expect(css).toContain(`background: ${BG.bg0}`);
    expect(css).not.toContain("linear-gradient(180deg, #0a0a0f");
  });
});

describe("antdTheme 槽位映射守卫", () => {
  it("13 槽位全映射：直引九槽等值+预混合双槽 alpha 复算+border 双槽直引等值", () => {
    const tk = antdThemeConfig.token!;
    expect(tk.colorBgBase).toBe(BG.bg0);
    expect(tk.colorText).toBe(TEXT.primary);
    expect(tk.colorTextSecondary).toBe(TEXT.secondary);
    expect(tk.colorTextTertiary).toBe(TEXT.tertiary);
    expect(tk.colorPrimary).toBe(ATTENTION.gold);
    expect(tk.colorSuccess).toBe(POLARITY.up);
    expect(tk.colorError).toBe(POLARITY.down);
    expect(tk.colorWarning).toBe(STATE.warn);
    // border 双槽=import BORDER 直引（hex8 原串）——引用等值断言
    expect(tk.colorBorder).toBe(BORDER.default);
    expect(tk.colorBorderSecondary).toBe(BORDER.line);
    // 预混合双槽：tokens 半透明值叠于 bg0 的实色——alpha 混合公式复算断言
    const blend = (fg: string, bg: string, alpha: number) => {
      const h = (s: string) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
      const [fr, fgc, fb] = h(fg);
      const [br, bgc, bb] = h(bg);
      const c = [fr, fgc, fb].map((f, i) => Math.round(f * alpha + [br, bgc, bb][i] * (1 - alpha)));
      return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`.toUpperCase();
    };
    // BG.bg1="#20224E66" alpha=0x66/255 叠 BG.bg0；BG.bg2="#2A2A5E99" alpha=0x99/255
    expect(tk.colorBgContainer).toBe(blend("#20224E", "#0D0F24", 0x66 / 255));
    expect(tk.colorBgElevated).toBe(blend("#2A2A5E", "#0D0F24", 0x99 / 255));
  });
});
