/**
 * 目标态设计令牌回归锚（CARD-02）：
 * BORDER.default 补键存在性+画布值+alpha 升序键位；全表 29 项计数对账
 * （头注声称 29 项——计数锚防未来扩容漏改头注，「枚举扩容计数残留」先例在案）。
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

  it("全表 29 项计数对账（与头注声称一致）", () => {
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
    ];
    const total = groups.reduce((n, g) => n + Object.keys(g).length, 0);
    expect(total).toBe(29);
  });
});
