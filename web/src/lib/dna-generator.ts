/**
 * Generate a minimal DNA from WHEN/THEN validation conditions.
 *
 * This is a lossy mapping: Lab conditions become entry/exit signal genes,
 * while full strategy DNA supports guards, logic, risk genes etc.
 * The generated DNA is sufficient for saving and basic backtesting.
 */
import type { ConditionInput, DNA, SignalGene } from "@/types/api";

/**
 * UI 动作名 → 后端 DNA 条件类型映射（fix-audit-correctness F-01 救活）。
 *
 * 后端 17 合法集（executor EVALUABLE_CONDITION_TYPES 同源快照）：
 * eq/lt/gt/le/ge/cross_above/cross_below/price_above/price_below/
 * cross_above_series/cross_below_series/lookback_any/lookback_all/
 * within_bars_after/touch_bounce/role_reversal/wick_touch。
 *
 * 不在本映射的动作（consecutive_up/consecutive_down/divergence_top/
 * divergence_bottom）后端无对应单阈值条件类型——禁存并提示，
 * 不做 "|| gt" 静默兜底（兜底曾把无映射动作静默改写为 gt 语义）。
 */
const CONDITION_TYPE_MAP: Record<string, string> = {
  gt: "gt",
  lt: "lt",
  ge: "ge",
  le: "le",
  eq: "eq",
  touch: "price_above",
  cross_above: "cross_above",
  cross_below: "cross_below",
  spike: "gt",
  shrink: "lt",
  breakout: "cross_above",
  breakdown: "cross_below",
  rise: "gt",
  drop: "lt",
};

/** 动作是否有可存映射（无映射动作禁存并提示）。 */
export function isActionSupported(action: string): boolean {
  return action in CONDITION_TYPE_MAP;
}

/** UI 动作名映射后端条件类型；无映射返回 null（禁存，不静默兜底）。 */
export function mapActionToCondType(action: string): string | null {
  return CONDITION_TYPE_MAP[action] ?? null;
}

/** 阈值数值化（后端 threshold 形制：int/float）；不可解析返回 null。 */
export function parseThreshold(target: unknown): number | null {
  if (typeof target === "number" && Number.isFinite(target)) return target;
  if (typeof target === "string") {
    const v = parseFloat(target);
    return Number.isNaN(v) ? null : v;
  }
  return null;
}

/** 校验 Lab 条件组可存性：返回不可存原因清单（空数组=可存）。 */
export function validateLabConditions(
  when: ReadonlyArray<Pick<ConditionInput, "action" | "target">>,
  then: ReadonlyArray<Pick<ConditionInput, "action" | "target">>,
): string[] {
  const problems: string[] = [];
  for (const [label, conds] of [
    ["入场", when],
    ["出场", then],
  ] as const) {
    for (const c of conds) {
      if (!isActionSupported(c.action)) {
        problems.push(
          `${label}条件动作「${c.action}」暂不支持保存为策略（无对应条件类型）`
        );
        continue;
      }
      const condType = mapActionToCondType(c.action)!;
      if (condType !== "price_above" && condType !== "price_below") {
        if (parseThreshold(c.target) === null) {
          problems.push(`${label}条件「${c.action}」阈值需为数字`);
        }
      }
    }
  }
  return problems;
}

function mapCondition(
  cond: ConditionInput,
  role: "entry_trigger" | "exit_trigger"
): SignalGene | null {
  const indicator = cond.subject || "close";
  const condType = mapActionToCondType(cond.action);
  if (condType === null) return null; // 无映射动作不产基因（禁存由调用方提示）
  const target = cond.target || "0";

  let params: Record<string, number> = {};
  // subject 混合形制收窄（0.2.13 起参数化指标为结构化对象——字符串
  // 兼容旧形；非字符串按未知指标走缺省参数族）
  const indicatorName = typeof indicator === "string"
    ? indicator : (indicator as { indicator?: string })?.indicator ?? "";
  // 从常见指标模式提取周期参数
  if (indicatorName.toLowerCase() === "rsi") {
    params = { period: 14 };
  } else if (indicatorName.toLowerCase() === "ema") {
    params = { period: 20 };
  } else if (indicatorName.toLowerCase() === "macd") {
    params = { fast: 12, slow: 26, signal: 9 };
  }

  // 构造条件字典
  // condType 经 CONDITION_TYPE_MAP 收敛到契约枚举；阈值经 threshold 字段承载
  // （后端 executor 消费键）；无阈值时回退固定 1 保持条件可求值
  const condTypeNarrow = condType as SignalGene["condition"]["type"];
  let condition: SignalGene["condition"];
  if (condType === "price_above" || condType === "price_below") {
    condition = { type: condTypeNarrow };
  } else {
    const threshold = parseFloat(target);
    condition = isNaN(threshold)
      ? { type: condTypeNarrow, threshold: 1 }
      : { type: condTypeNarrow, threshold };
  }

  return {
    indicator: indicatorName.toUpperCase(),
    params,
    role,
    field: null,
    condition,
  };
}

export function generateDnaFromValidation(
  when: ConditionInput[],
  then: ConditionInput[],
  pair: string,
  timeframe: string
): DNA {
  // WHEN 条件映射为入场触发器；THEN 条件映射为出场触发器。
  // 无映射动作 mapCondition 返回 null——过滤掉（禁存判定与提示由
  // validateLabConditions 承载，本函数仅保证产出的基因全部合法）
  const signal_genes = [
    ...when
      .filter((c) => c.subject)
      .map((c) => mapCondition(c, "entry_trigger")),
    ...then
      .filter((c) => c.subject)
      .map((c) => mapCondition(c, "exit_trigger")),
  ].filter((g): g is SignalGene => g !== null);

  return {
    signal_genes,
    logic_genes: {
      entry_logic: "AND",
      exit_logic: "AND",
    },
    execution_genes: {
      timeframe: timeframe,
      symbol: pair,
    },
    risk_genes: {
      stop_loss: 0.05,
      take_profit: 0.1,
      position_size: 0.3,
      leverage: 1,
      direction: "long",
    },
    strategy_id: crypto.randomUUID(),
    generation: 0,
    parent_ids: [],
    mutation_ops: [],
  };
}
