import { cn } from "@/lib/utils";
import { STATE, LIVE_BADGE, type BadgeStatus } from "@/theme/tokens";

/**
 * G-1 状态徽标（global-nav-shell）——形制核取自原型画布 G-1 frame：
 * 透明底（#FFFFFF0F 染层）+1px 描边+6×6 状态点+fs11 标签，描边/文本/点三处同源状态色。
 * live 为唯一实心例外：live-badge-bg 底+live-badge-text 字（无点差异被底色吸收）。
 */
export type StatusBadgeSize = "sm" | "md";

interface StatusBadgeProps {
  status: BadgeStatus;
  label?: string;
  size?: StatusBadgeSize;
  className?: string;
}

const SIZES: Record<StatusBadgeSize, { box: string; dot: string; font: string }> = {
  sm: { box: "gap-[5px] px-2 py-[3px]", dot: "h-1.5 w-1.5", font: "text-[11px]" },
  md: { box: "gap-[5px] px-2.5 py-1", dot: "h-[7px] w-[7px]", font: "text-xs" },
};

export function StatusBadge({ status, label, size = "sm", className }: StatusBadgeProps) {
  const text = label ?? status;
  const s = SIZES[size];

  if (status === "live") {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-lg font-normal leading-none",
          s.box,
          s.font,
          className
        )}
        style={{ backgroundColor: LIVE_BADGE.bg, color: LIVE_BADGE.text }}
      >
        {text}
      </span>
    );
  }

  const color = STATE[status];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-lg font-normal leading-none",
        s.box,
        s.font,
        className
      )}
      style={{ color, border: `1px solid ${color}`, backgroundColor: "#FFFFFF0F" }}
    >
      <span className={cn("rounded-full", s.dot)} style={{ backgroundColor: color }} />
      {text}
    </span>
  );
}
