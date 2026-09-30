import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { SubjectDropdown } from "./SubjectDropdown";
import { ActionDropdown } from "./ActionDropdown";
import { TargetInput } from "./TargetInput";
import type { RuleCondition } from "@/types/api";

type LogicValue = RuleCondition["logic"];

const LOGIC_OPTIONS: LogicValue[] = ["IF", "AND", "OR"];

interface RuleConditionRowProps {
  condition: RuleCondition;
  onChange: (updated: RuleCondition) => void;
  onDelete: () => void;
  isFirst: boolean;
  availableTimeframes: string[];
}

export function RuleConditionRow({
  condition,
  onChange,
  onDelete,
  isFirst,
  availableTimeframes,
}: RuleConditionRowProps) {
  const handleLogicChange = (logic: LogicValue) => {
    onChange({ ...condition, logic });
  };

  const handleTimeframeChange = (timeframe: string) => {
    onChange({ ...condition, timeframe });
  };

  const handleSubjectChange = (subject: string) => {
    // Reset action and target when subject changes
    onChange({ ...condition, subject, action: "", target: "" });
  };

  const handleActionChange = (action: string) => {
    // Reset target when action changes
    onChange({ ...condition, action, target: "" });
  };

  const handleTargetChange = (target: string) => {
    onChange({ ...condition, target });
  };

  return (
    <div className="flex items-center gap-2 rounded-lg border border-border-default bg-bg-surface px-2 py-1">
      {/* Logic selector: IF / AND / OR */}
      <div className="flex shrink-0 items-center gap-0.5">
        {LOGIC_OPTIONS.map((logic) => {
          const isSelected = isFirst ? logic === "IF" : condition.logic === logic;
          const isDisabled = isFirst ? logic !== "IF" : false;

          return (
            <button
              key={logic}
              type="button"
              disabled={isDisabled}
              className={cn(
                "rounded px-1.5 py-0.5 text-xs font-medium transition-colors",
                isSelected
                  ? "text-accent-gold bg-accent-gold/10"
                  : "text-text-muted hover:text-text-primary",
                isDisabled && "cursor-not-allowed opacity-30",
              )}
              onClick={() => handleLogicChange(logic)}
            >
              {logic}
            </button>
          );
        })}
      </div>

      {/* Timeframe selector */}
      {/* 0.2.14：timeframe 键真实生效（掩码投影——仅已收盘高周期 K 线可见）；
          穿越类动作挂高周期为状态窗语义（一次穿越持续至下次高周期收盘） */}
      <select
        title="周期修饰整条件：在高周期上求值后按已收盘可见性投影回基准轴；穿越类动作为状态窗（持续至下次高周期收盘）"
        value={condition.timeframe}
        onChange={(e) => handleTimeframeChange(e.target.value)}
        className={cn(
          "h-7 shrink-0 rounded-md border border-border-default bg-bg-surface px-1.5 text-xs text-text-primary outline-none",
          "hover:border-accent-gold/50 focus:border-accent-gold",
        )}
      >
        {availableTimeframes.map((tf) => (
          <option key={tf} value={tf}>
            {tf}
          </option>
        ))}
      </select>

      {/* Subject dropdown */}
      <SubjectDropdown value={condition.subject} onChange={handleSubjectChange} />

      {/* Action dropdown */}
      <ActionDropdown
        value={condition.action}
        onChange={handleActionChange}
        subject={condition.subject}
        isThen={false}
      />

      {/* Target input */}
      <TargetInput
        value={condition.target}
        onChange={handleTargetChange}
        action={condition.action}
        subject={condition.subject}
        baseTimeframe={condition.timeframe}
        referencedTimeframes={availableTimeframes}
      />

      {/* Delete button */}
      <button
        type="button"
        onClick={onDelete}
        className={cn(
          "ml-auto shrink-0 rounded p-1 text-text-muted transition-colors",
          "hover:bg-red-500/10 hover:text-red-400",
        )}
        aria-label="delete condition"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
