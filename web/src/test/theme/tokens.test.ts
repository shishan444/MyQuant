/**
 * 目标态设计令牌回归锚（L1 契约单元层，CARD-02；fe-shell-theme-cutover 扩容）：
 * BORDER.default 补键存在性+画布值+alpha 升序键位；全表 46 项计数对账（头注声称
 * 46 项=主体系 29+图表三色板 17——计数锚防扩容漏改头注，「枚举扩容计数残留」先例
 * 在案）；图表三色板三族键成员枚举（exact key set——扩容防护强于裸计数）。
 */
import { describe, it, expect } from "vitest";
import {
  BG,
  BORDER,
  TEXT,
  POLARITY,
  STATE,
  ATTENTION,
  DOMAIN,
  LIVE_BADGE,
  FONT,
  RADIUS,
  CHART_VA,
  CHART_VB,
  CHART_VC,
} from "@/theme/tokens";

describe("tokens（global-nav-shell 目标态令牌）", () => {
  it("BORDER 五档齐全，default=画布实证 #FFFFFF1A，键序按 alpha 升序", () => {
    expect(Object.keys(BORDER)).toEqual([
      "faint",
      "line",
      "default",
      "strong",
      "selected",
    ]);
    expect(BORDER.default).toBe("#FFFFFF1A");
  });

  it("全表 46 项计数对账（与头注声称一致：主体系 29+图表三色板 17）", () => {
    const groups = [
      BG,
      BORDER,
      TEXT,
      POLARITY,
      STATE,
      ATTENTION,
      DOMAIN,
      LIVE_BADGE,
      FONT,
      RADIUS,
      CHART_VA,
      CHART_VB,
      CHART_VC,
    ];
    const total = groups.reduce((n, g) => n + Object.keys(g).length, 0);
    expect(total).toBe(46);
  });

  it("图表三色板三族键成员枚举（exact key set——变体扩容必红）", () => {
    expect(Object.keys(CHART_VA)).toEqual([
      "bg",
      "panel",
      "line",
      "amber",
      "text",
      "dim",
    ]);
    expect(Object.keys(CHART_VB)).toEqual([
      "bg",
      "panel",
      "panel2",
      "blue",
      "text",
      "dim",
    ]);
    expect(Object.keys(CHART_VC)).toEqual([
      "bg",
      "float",
      "teal",
      "text",
      "dim",
    ]);
  });
});
