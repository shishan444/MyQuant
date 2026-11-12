import { useMemo } from "react";
import { OptionDropdown } from "./OptionDropdown";

interface ActionOption {
  value: string;
  label: string;
  category: string;
}

const PRICE_ACTIONS: ActionOption[] = [
  { value: "touch", label: "触及", category: "穿越类" },
  { value: "cross_above", label: "上穿", category: "穿越类" },
  { value: "cross_below", label: "下穿", category: "穿越类" },
  { value: "breakout", label: "突破", category: "突破类" },
  { value: "breakdown", label: "跌破", category: "突破类" },
  { value: "gt", label: "大于", category: "比较类" },
  { value: "lt", label: "小于", category: "比较类" },
  { value: "ge", label: "大于等于", category: "比较类" },
  { value: "le", label: "小于等于", category: "比较类" },
  { value: "consecutive_up", label: "连涨N根", category: "连续类" },
  { value: "consecutive_down", label: "连跌N根", category: "连续类" },
];

const VOLUME_ACTIONS: ActionOption[] = [
  { value: "spike", label: "放量", category: "量能" },
  { value: "shrink", label: "缩量", category: "量能" },
  { value: "gt", label: "大于", category: "比较" },
  { value: "lt", label: "小于", category: "比较" },
];

const INDICATOR_ACTIONS: ActionOption[] = [
  { value: "gt", label: "大于", category: "比较类" },
  { value: "lt", label: "小于", category: "比较类" },
  { value: "ge", label: "大于等于", category: "比较类" },
  { value: "le", label: "小于等于", category: "比较类" },
  { value: "cross_above", label: "上穿", category: "穿越类" },
  { value: "cross_below", label: "下穿", category: "穿越类" },
  { value: "cross_above_series", label: "上穿指标线", category: "穿越类" },
  { value: "cross_below_series", label: "下穿指标线", category: "穿越类" },
  { value: "touch", label: "触及", category: "穿越类" },
  { value: "lookback_any", label: "回溯N根满足", category: "回溯类" },
  { value: "lookback_all", label: "回溯N根全满足", category: "回溯类" },
  { value: "divergence_top", label: "顶背离", category: "形态类" },
  { value: "divergence_bottom", label: "底背离", category: "形态类" },
  { value: "touch_bounce", label: "触碰反弹", category: "支撑压力类" },
  { value: "role_reversal", label: "角色转换", category: "支撑压力类" },
];

const THEN_DIRECTION_ACTIONS: ActionOption[] = [
  { value: "drop", label: "下跌", category: "方向" },
  { value: "rise", label: "上涨", category: "方向" },
];

const INDICATOR_PATTERN_SUBJECTS = new Set(["rsi", "macd", "kdj", "stoch", "cci", "roc"]);

function getActionsForSubject(subject: string, isThen: boolean): ActionOption[] {
  if (isThen) {
    if (subject === "close" || subject === "price" || subject === "open" || subject === "high" || subject === "low") {
      return THEN_DIRECTION_ACTIONS;
    }
    return INDICATOR_ACTIONS.filter(
      (a) => a.category !== "形态类" || INDICATOR_PATTERN_SUBJECTS.has(subject),
    );
  }

  if (["close", "open", "high", "low", "price"].includes(subject)) {
    return PRICE_ACTIONS;
  }
  if (subject === "volume") {
    return VOLUME_ACTIONS;
  }

  // 指标主体：仅振荡类指标保留形态动作
  if (INDICATOR_PATTERN_SUBJECTS.has(subject)) {
    return INDICATOR_ACTIONS;
  }
  return INDICATOR_ACTIONS.filter((a) => a.category !== "形态类");
}

const LABEL_MAP: Record<string, string> = {};
const allActions = [...PRICE_ACTIONS, ...VOLUME_ACTIONS, ...INDICATOR_ACTIONS, ...THEN_DIRECTION_ACTIONS,
  // 补充未重复的新动作供标签映射
  { value: "touch_bounce", label: "触碰反弹", category: "支撑压力类" },
  { value: "role_reversal", label: "角色转换", category: "支撑压力类" },
  { value: "cross_above_series", label: "上穿指标线", category: "穿越类" },
  { value: "cross_below_series", label: "下穿指标线", category: "穿越类" },
  { value: "lookback_any", label: "回溯N根满足", category: "回溯类" },
  { value: "lookback_all", label: "回溯N根全满足", category: "回溯类" },
];
for (const opt of allActions) {
  LABEL_MAP[opt.value] = opt.label;
}

export function getActionLabel(value: string): string {
  return LABEL_MAP[value] ?? value;
}

interface ActionDropdownProps {
  value: string;
  onChange: (v: string) => void;
  subject: string;
  isThen?: boolean;
}

export function ActionDropdown({ value, onChange, subject, isThen = false }: ActionDropdownProps) {
  const actions = useMemo(() => getActionsForSubject(subject, isThen), [subject, isThen]);
  return (
    <OptionDropdown
      value={value}
      label={getActionLabel(value)}
      options={actions}
      onChange={onChange}
      width={144}
      maxHeightClass="max-h-48"
    />
  );
}
