import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { RuleConditionRow } from "./RuleConditionRow";
import type { RuleCondition } from "@/types/api";

interface RuleConditionGroupProps {
  title: string;
  description: string;
  conditions: RuleCondition[];
  onConditionsChange: (conditions: RuleCondition[]) => void;
  availableTimeframes: string[];
}

const DEFAULT_TIMEFRAME = "15m";

function createDefaultCondition(
  isFirst: boolean,
  availableTimeframes: string[],
): RuleCondition {
  return {
    logic: isFirst ? "IF" : "AND",
    timeframe: availableTimeframes[availableTimeframes.length - 1] || DEFAULT_TIMEFRAME,
    subject: "",
    action: "",
    target: "",
  };
}

export function RuleConditionGroup({
  title,
  description,
  conditions,
  onConditionsChange,
  availableTimeframes,
}: RuleConditionGroupProps) {
  const handleAdd = () => {
    const next = [
      ...conditions,
      createDefaultCondition(conditions.length === 0, availableTimeframes),
    ];
    onConditionsChange(next);
  };

  const handleChange = (index: number, updated: RuleCondition) => {
    const next = [...conditions];
    next[index] = updated;
    onConditionsChange(next);
  };

  const handleDelete = (index: number) => {
    const next = conditions.filter((_, i) => i !== index);
    // 确保余下首条条件的逻辑词为 IF
    if (next.length > 0 && next[0].logic !== "IF") {
      next[0] = { ...next[0], logic: "IF" };
    }
    onConditionsChange(next);
  };

  return (
    <div
      className={cn(
        "rounded-xl border border-border-default bg-bg-surface/60 p-4",
        "backdrop-blur-md",
      )}
    >
      {/* Header */}
      <div className="mb-3 flex items-center">
        <span className="text-sm font-medium text-text-primary">{title}</span>
        <span className="ml-2 text-xs text-text-muted">{description}</span>
      </div>

      {/* Condition rows */}
      <div className="flex flex-col gap-2">
        {conditions.length === 0 && (
          <div className="py-6 text-center text-xs text-text-muted">
            点击 + 添加条件 开始配置规则
          </div>
        )}

        {conditions.map((condition, index) => (
          <RuleConditionRow
            key={index}
            condition={condition}
            onChange={(updated) => handleChange(index, updated)}
            onDelete={() => handleDelete(index)}
            isFirst={index === 0}
            availableTimeframes={availableTimeframes}
          />
        ))}
      </div>

      {/* Add button */}
      <button
        type="button"
        onClick={handleAdd}
        className={cn(
          "mt-3 flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors",
          "text-text-muted hover:text-accent-gold",
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        添加条件
      </button>
    </div>
  );
}
