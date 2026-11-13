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
import { CHART_COLORS } from "@/components/charts/core/chartThemes";

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
