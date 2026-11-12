/**
 * toTime 三档形制域（upgrade-vr-fullrange-chart 契约 G）：
 * ①带偏移按偏移 ②裸串按 UTC（变更点）③malformed 日期串回退；
 * 属性不变式：同一时刻裸串与带偏移串产出相同 epoch 且与环境时区无关
 * （与 Date.UTC 显式构造对照——若解析走本地时区，非 UTC 环境下必红）。
 */
import { describe, expect, it } from "vitest";
import { toTime } from "@/components/charts/core/time";

const EPOCH = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);

describe("toTime 三档形制域", () => {
  it("①带偏移串按偏移解析（Z 与 ±HH:MM 两种尾缀）", () => {
    expect(toTime("2025-11-04T00:00:00+00:00")).toBe(EPOCH("2025-11-04T00:00:00Z"));
    expect(toTime("2025-11-04T08:00:00+08:00")).toBe(EPOCH("2025-11-04T00:00:00Z"));
    expect(toTime("2025-11-04 00:00:00+00:00")).toBe(EPOCH("2025-11-04T00:00:00Z"));
  });

  it("②裸串按 UTC 解析——与显式 Date.UTC 构造逐位一致（时区无关锚）", () => {
    const expected = Date.UTC(2025, 10, 4, 0, 0, 0) / 1000;
    expect(toTime("2025-11-04T00:00:00")).toBe(expected);
    expect(toTime("2025-11-04 00:00:00")).toBe(expected);
    // date-only 裸串：ES 规范本就 UTC，与 datetime 形同锚
    expect(toTime("2025-11-04")).toBe(expected);
  });

  it("③malformed 串 slice(0,10) 日期串回退", () => {
    expect(toTime("not-a-date")).toBe("not-a-date".slice(0, 10));
    expect(toTime("garbage!!")).toBe("garbage!!".slice(0, 10));
  });

  it("属性不变式：同一时刻裸串与带偏移串产出相同 epoch", () => {
    expect(toTime("2025-11-04T00:00:00")).toBe(toTime("2025-11-04T00:00:00+00:00"));
    expect(toTime("2025-11-04T00:00:00")).toBe(toTime("2025-11-04T08:00:00+08:00"));
  });

  it("偏移尾缀 pattern 不误伤：秒字段/负号不构成偏移", () => {
    // 无秒冒号结构的裸串不会被 pattern 命中（无尾缀偏移形）
    expect(toTime("2025-11-04T12:34:56")).toBe(
      Date.UTC(2025, 10, 4, 12, 34, 56) / 1000,
    );
  });
});
