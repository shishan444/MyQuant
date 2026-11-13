/**
 * 蜡烛与信号色单源锚+治理域字面扫描（fe-polarity-calibration spec 场景
 * 「蜡烛色单源」「衍生色无字面副本」的机器承载，G1）：
 * candleUp/candleDown（含 buySignal/sellSignal 同族）恒取 POLARITY 单源；
 * 治理域（chartThemes.ts）内涨跌色值零字面第二实现。扫描豁免面（CARD-05
 * 勘误锁定）：KlineChart.tsx/positionBand.ts 存量五处 rgba 衍生（T2 注在位）
 * 属 U1 保持项豁免，不在本扫描域。
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { POLARITY } from "@/theme/tokens";
import { CHART_COLORS, withAlpha } from "@/components/charts/core/chartThemes";

describe("蜡烛与信号色单源（G1：蜡烛恒取 POLARITY）", () => {
  it("candleUp/candleDown/buySignal/sellSignal === POLARITY 引用等值", () => {
    expect(CHART_COLORS.candleUp).toBe(POLARITY.up);
    expect(CHART_COLORS.candleDown).toBe(POLARITY.down);
    expect(CHART_COLORS.buySignal).toBe(POLARITY.up);
    expect(CHART_COLORS.sellSignal).toBe(POLARITY.down);
  });

  it("治理域扫描：chartThemes.ts 零涨跌色字面形（hex 大小写不敏感+rgb 元组）", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../../../components/charts/core/chartThemes.ts"),
      "utf-8",
    );
    // POLARITY 两键的四种字面书写形：hex6 大小写两形+rgb 元组形（withAlpha
    // 函数体为模板串拼接不含具体色值，天然不命中）
    const literals = [
      /#2dd4bf/i,
      /#fb7185/i,
      /45,\s*212,\s*191/,
      /251,\s*113,\s*133/,
    ];
    for (const re of literals) {
      expect(source.match(re), `chartThemes.ts 不应含涨跌色字面形 ${re}`).toBeNull();
    }
  });
});

describe("withAlpha 纯函数直测（#RRGGBB + alpha → rgba 换算）", () => {
  // 独立核算前提：EquityLineChart/ScoreTrendChart 渐变与半透明色断言以
  // withAlpha 生成期望值，其自身正确性须有独立样例锚（不用被测实现生成期望）
  it("确定样例：人工核算三例（hex 三段十六进制逐值对回）", () => {
    // #2dd4bf: 2d=45, d4=212, bf=191；#fb7185: fb=251, 71=113, 85=133
    expect(withAlpha("#2dd4bf", 0.3)).toBe("rgba(45,212,191,0.3)");
    expect(withAlpha("#fb7185", 0)).toBe("rgba(251,113,133,0)");
    expect(withAlpha("#000000", 1)).toBe("rgba(0,0,0,1)");
  });

  it("不变量：alpha 透传与分量还原（输出 rgb 恒等 hex 三段 parseInt）", () => {
    // 对任意 #RRGGBB 与 alpha：输出尾段恒为 alpha 本值，rgb 分量与
    // parseInt(hex 段, 16) 逐值一致（独立推导路径对回）
    for (const [hex, alpha] of [
      ["#10B981", 0.15],
      ["#94A3B8", 0.5],
      ["#FFFFFF", 1],
    ] as const) {
      const out = withAlpha(hex, alpha);
      expect(out.endsWith(`,${alpha})`)).toBe(true);
      const [r, g, b] = out.slice(5, -3).split(",").map(Number);
      expect(r).toBe(parseInt(hex.slice(1, 3), 16));
      expect(g).toBe(parseInt(hex.slice(3, 5), 16));
      expect(b).toBe(parseInt(hex.slice(5, 7), 16));
    }
  });
});
