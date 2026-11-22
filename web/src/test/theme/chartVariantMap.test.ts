/**
 * 图表三色板页面变体映射锚（fe-polarity-calibration spec 场景「变体按表
 * 消费」的机器承载，G2）：CHART_VARIANT_MAP 表值精确快照（改错/键增删必红）
 * +五消费组件源文本 import 追溯（消费族 === MAP 键值）。消费清单为测试
 * 自身维护的显式枚举——新消费组件入清单是测试维护义务。
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { CHART_VARIANT_MAP } from "@/theme/tokens";

describe("CHART_VARIANT_MAP 表值锚（G2：单一权威映射表）", () => {
  it("三键三值精确快照（表值改错/键增删必红；05-evolution 随 U1、08-live 随 U6 退役删除）", () => {
    expect(CHART_VARIANT_MAP).toEqual({
      "07-trading": "va",
      "08-b6r-decisions": "vb",
      "03-validation-review": "vc",
    });
  });
});

describe("消费面追溯（G2：各页按表消费）", () => {
  // MAP 键 → 消费组件文件（相对 web/src 根）；import 断言形：
  // import { …CHART_V<x>… } from "@/theme/tokens"
  const consumers: Array<[keyof typeof CHART_VARIANT_MAP, string[]]> = [
    ["07-trading", ["components/trading/ChartHud.tsx"]],
    ["08-b6r-decisions", ["components/validationRecords/traceSemantics.ts"]],
    ["03-validation-review", ["components/validationRecords/RecordDetail.tsx"]],
  ];

  it.each(consumers)("%s 消费组件 import 变体族与 MAP 一致", (page, files) => {
    const variantToken = `CHART_${CHART_VARIANT_MAP[page].toUpperCase()}`;
    const importRe = new RegExp(
      `import\\s*\\{[^}]*\\b${variantToken}\\b[^}]*\\}\\s*from\\s*"@/theme/tokens"`,
    );
    for (const f of files) {
      const source = readFileSync(
        join(dirname(fileURLToPath(import.meta.url)), "../../", f),
        "utf-8",
      );
      expect(source.match(importRe), `${f} 应 import ${variantToken}（按表消费）`).not.toBeNull();
    }
  });
});
