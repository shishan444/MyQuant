/**
 * 极性对比度锚三件套（fe-polarity-calibration T2.1/T2.2/T2.7，G4）：
 * 公式=WCAG 2.1 相对亮度+对比度+alpha 合成，测试内嵌独立实现（对照
 * change 工件 contrast-report.md 复算脚本逐行翻译，禁以被测实现生成期望
 * ——web 侧无对比度生产实现，独立内嵌即天然满足）；色值输入=import
 * tokens 单源。表值与合成 alpha（0.402/0.596/0.8）取 contrast-report.md
 * 报告原文。撞值现状（STATE.ok=#2DD4BF 与 POLARITY.up 同值）=组 3 裁决
 * 登记项，T2.3 维持现值分支登记不红灯，不入硬断言。
 */
import { describe, it, expect } from "vitest";
import { BG, POLARITY, STATE, CHART_VA, CHART_VB, CHART_VC } from "@/theme/tokens";

// ── 独立公式实现（WCAG 2.1；contrast-report.md 附录同款） ──
const lum = (hex: string): number => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const adj = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * adj(r) + 0.7152 * adj(g) + 0.0722 * adj(b);
};
const contrast = (c1: string, c2: string): number => {
  const l1 = lum(c1);
  const l2 = lum(c2);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
};
const blend = (fgHex: string, alpha: number, bgHex: string): string => {
  const f = [1, 3, 5].map((i) => parseInt(fgHex.slice(i, i + 2), 16));
  const b = [1, 3, 5].map((i) => parseInt(bgHex.slice(i, i + 2), 16));
  const m = f.map((v, i) => Math.round(v * alpha + b[i] * (1 - alpha)));
  return `#${m.map((v) => v.toString(16).padStart(2, "0")).join("")}`.toUpperCase();
};
const hueOf = (hex: string): number => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d === 0) return 0;
  let h: number;
  if (max === r) h = 60 * (((g - b) / d) % 6);
  else if (max === g) h = 60 * ((b - r) / d + 2);
  else h = 60 * ((r - g) / d + 4);
  return (h + 360) % 360;
};

// ── 背景层全集（六层；合成 alpha=报告复算脚本同款字面） ──
const BG1_A = 0.402; // BG.bg1 hex8 尾 0x66
const BG2_A = 0.596; // BG.bg2 hex8 尾 0x99
const LW_A = 0.8; // lightweight 背景透明度（DARK_CHART_THEME rgba(13,17,23,0.8)）
const LW_BASE = "#0D1117"; // rgb(13,17,23)
const bg0 = BG.bg0;
const lw = blend(LW_BASE, LW_A, bg0);
const backgrounds: Array<[string, string]> = [
  ["BG.bg0", bg0],
  ["BG.bg1@bg0", blend(BG.bg1.slice(0, 7), BG1_A, bg0)],
  ["BG.bg2@bg0", blend(BG.bg2.slice(0, 7), BG2_A, bg0)],
  ["lightweight 合成底", lw],
  ["BG.bg1@lw", blend(BG.bg1.slice(0, 7), BG1_A, lw)],
  ["BG.bg2@lw", blend(BG.bg2.slice(0, 7), BG2_A, lw)],
];

// ── 报告表值（contrast-report.md 现值 10 格原文；候选四档 worst） ──
const REPORT_TABLE: Array<[string, number, number]> = [
  ["BG.bg0", 10.155, 7.024],
  ["BG.bg1@bg0", 9.355, 6.471],
  ["BG.bg2@bg0", 8.405, 5.813],
  ["lightweight 合成底", 10.143, 7.015],
  ["BG.bg1@lw", 9.373, 6.483],
  ["BG.bg2@lw", 8.401, 5.810],
];
const CANDIDATES: Array<[string, string, number]> = [
  ["teal-200", "#99F6E4", 12.404],
  ["teal-500", "#14B8A6", 6.283],
  ["rose-200", "#FECDD3", 11.086],
  ["rose-500", "#F43F5E", 4.259],
];

describe("对比度下限锚（T2.1：六背景×两键全 ≥3:1）", () => {
  it.each(backgrounds)("%s：up/down 对比度均 ≥3:1", (_name, bg) => {
    expect(contrast(POLARITY.up, bg)).toBeGreaterThanOrEqual(3);
    expect(contrast(POLARITY.down, bg)).toBeGreaterThanOrEqual(3);
  });
});

describe("报告表值复算锚（T2.7：公式复算与 contrast-report 逐格吻合）", () => {
  it.each(REPORT_TABLE)("%s 表值 up=%s down=%s 复算吻合", (name, upVal, downVal) => {
    const bg = backgrounds.find(([n]) => n === name)?.[1];
    expect(bg, `背景 ${name} 在背景层全集中`).toBeDefined();
    expect(contrast(POLARITY.up, bg!)).toBeCloseTo(upVal, 3);
    expect(contrast(POLARITY.down, bg!)).toBeCloseTo(downVal, 3);
  });

  it("候选四档 worst（六背景最小对比度）与报告存档值吻合", () => {
    for (const [name, hex, worst] of CANDIDATES) {
      const min = Math.min(...backgrounds.map(([, bg]) => contrast(hex, bg)));
      expect(min, `${name} 六背景 worst`).toBeCloseTo(worst, 3);
    }
  });
});

describe("对比度下限锚·三色板底补全（T2.1：design D1 背景层全集）", () => {
  // D1 契约面：背景层全集=bg0+三色板底+lightweight 合成底。三色板底是 K 线
  // 蜡烛（恒取 POLARITY）的实际底面（va=05/07、vb=08、vc=03——CHART_VARIANT_MAP），
  // contrast-report.md 实算六层未含此三底（D-1 偏差登记 tester3.md）——本组
  // 按 D1 补全：实测 up=10.949/10.159/10.440，down=7.573/7.027/7.221。
  const chartBgs: Array<[string, string]> = [
    ["CHART_VA.bg", CHART_VA.bg],
    ["CHART_VB.bg", CHART_VB.bg],
    ["CHART_VC.bg", CHART_VC.bg],
  ];
  it.each(chartBgs)("%s：up/down 对比度均 ≥3:1", (_name, bg) => {
    expect(contrast(POLARITY.up, bg)).toBeGreaterThanOrEqual(3);
    expect(contrast(POLARITY.down, bg)).toBeGreaterThanOrEqual(3);
  });
});

describe("候选档零撞 STATE 八态（T2.2 附列判据：tasks 2.1「零撞 STATE 八态」机器承载）", () => {
  it("四候选 hex ∉ STATE 八态值集", () => {
    // Set<string> 显式放宽：has 查询域=任意 hex 字符串（STATE 字面量联合
    // 仅集合成员推导——不放宽则 has(string) TS2345 类型面红，闸门收口）
    const stateVals = new Set<string>(Object.values(STATE));
    for (const [name, hex] of CANDIDATES) {
      expect(stateVals.has(hex), `${name} ${hex} 不得撞 STATE 八态`).toBe(false);
    }
  });
});

describe("撞值现状登记（T2.3 维持分支：登记不红灯）", () => {
  // 组 3.2 裁决留存：STATE.ok=#2DD4BF 与 POLARITY.up 同值=已登记撞值现状
  // （STATE 拆分后续素材）。本锚=登记形态快照：形态未经组 4 流程变化
  // （拆分/单侧改值）必红提示复核；STATE 拆分 change 落地时移除本锚。
  it("登记形态：STATE.ok 与 POLARITY.up 同值（组 3.2 登记项在案）", () => {
    expect(STATE.ok).toBe(POLARITY.up);
  });
});

describe("色相族锚（T2.2：up 落 teal 带、down 落 rose 带）", () => {
  // 带界按 tailwind teal/rose 族 200~500 档实测 hue 域校准放边
  it("POLARITY.up hue ∈ [160,185]（teal 色相带）", () => {
    const h = hueOf(POLARITY.up);
    expect(h).toBeGreaterThanOrEqual(160);
    expect(h).toBeLessThanOrEqual(185);
  });
  it("POLARITY.down hue ∈ [335,360)（rose 色相带）", () => {
    const h = hueOf(POLARITY.down);
    expect(h).toBeGreaterThanOrEqual(335);
    expect(h).toBeLessThan(360);
  });
});
