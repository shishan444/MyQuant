/**
 * 条件类型 logic 枚举三套一致性守卫（fe-type-contract-consolidation R-8c）。
 *
 * 三套界定：
 * - 套1＝types/api.ts ConditionInput["logic"]（"AND"|"OR"）
 * - 套2＝types/api.ts RuleCondition["logic"]（"IF"|"AND"|"OR"）
 * - 套3＝components/lab/RuleConditionRow.tsx LOGIC_OPTIONS 运行时数组
 *   （模块私有未导出——经 logic 按钮区 DOM 观测，三套定义文件零改动）
 *
 * 守卫两断言：套1⊂套2（子集）；套3 运行时值集==套2 类型全集。
 * 漂移拦截通路：套1/套2 类型漂移→下方钉死数组的互穷尽编译断言红
 * （tsc -b / build 挡——vitest 转译不查类型）；套3 数组漂移（减值/
 * 加值/换值）→DOM 值集对照运行时断言红（vitest 挡）。
 */
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { ConditionInput, RuleCondition } from "@/types/api";
import { RuleConditionRow } from "@/components/lab/RuleConditionRow";

// —— 钉死数组：与类型互为穷尽（多值/少值均编译红）——
const SET1 = ["AND", "OR"] as const satisfies readonly ConditionInput["logic"][];
const SET2 = ["IF", "AND", "OR"] as const satisfies readonly RuleCondition["logic"][];

// 套1 互穷尽：SET1 与 ConditionInput["logic"] 成员完全一致
type _Set1Exact = [ConditionInput["logic"]] extends [typeof SET1[number]]
  ? [typeof SET1[number]] extends [ConditionInput["logic"]]
    ? true
    : never
  : never;
const _set1Exact: _Set1Exact = true;

// 套2 互穷尽：SET2 与 RuleCondition["logic"] 成员完全一致
type _Set2Exact = [RuleCondition["logic"]] extends [typeof SET2[number]]
  ? [typeof SET2[number]] extends [RuleCondition["logic"]]
    ? true
    : never
  : never;
const _set2Exact: _Set2Exact = true;

// 套1⊂套2 子集：ConditionInput 是简化规则条件，其 logic 二值必须是
// RuleCondition 三值的子集（行首 IF 只属规则条件面）
type _Set1SubsetOfSet2 = typeof SET1[number] extends RuleCondition["logic"] ? true : never;
const _set1Subset: _Set1SubsetOfSet2 = true;

// 编译断言位消费（TS6133 消除——断言仅在类型层生效，运行时无值）
void _set1Exact;
void _set2Exact;
void _set1Subset;

describe("条件 logic 枚举三套一致性守卫（R-8c）", () => {
  it("套3 LOGIC_OPTIONS 运行时值集==套2 类型全集（logic 按钮区 DOM 值集对照）", () => {
    const { container } = render(
      <RuleConditionRow
        condition={{ logic: "AND", timeframe: "4h", subject: "close", action: "gt", target: "10" }}
        onChange={vi.fn()}
        onDelete={vi.fn()}
        isFirst={false}
        availableTimeframes={["4h"]}
      />,
    );
    // logic 按钮区＝根容器首个子块（RuleConditionRow 渲染形制：
    // "Logic selector: IF / AND / OR" 块内 LOGIC_OPTIONS.map，按钮
    // 文本=枚举值本身）
    const logicBox = container.firstElementChild?.firstElementChild;
    const observed = Array.from(logicBox?.querySelectorAll("button") ?? []).map(
      (b) => b.textContent?.trim() ?? "",
    );
    // 全集+无多（toEqual 严格对照：减值/加值/换值均红）
    expect(observed).toEqual([...SET2]);
  });

  it("套1⊂套2 运行时镜像（钉死数组承载——编译断言的 vitest 可见绿行）", () => {
    const set2 = new Set<string>(SET2);
    for (const v of SET1) {
      expect(set2.has(v)).toBe(true);
    }
  });
});
