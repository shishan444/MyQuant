/**
 * verify_star 列（1-5 制）星级呈现单一公共组件（fe-research-execution-domain
 * StarRating 升格卡——蓝本 MiniStars 五槽；产出八着色终态）。
 * 着色：lit>=4 → #FFC76B（ATTENTION.gold 组件内 import——tokens 生产消费
 * 第三文件先例）；lit>=3 → text-blue-400 utility（D-arch5 合法终态，⑤收尾
 * 扫描不命中类名）；其余 → text-text-muted（@theme 对齐后自动 #78739B）。
 * 门槛达标数组件（VerifyStar 0-3 制）为独立语义不并入（D-arch2 修正）。
 */
import { Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { ATTENTION } from "@/theme/tokens";

export function StarRating({ value }: { value?: number | null }) {
  const n = value ?? 0;
  return (
    <span
      className={cn(
        "flex items-center justify-center gap-px text-[10px]",
        n >= 4 ? "" : n >= 3 ? "text-blue-400" : "text-text-muted",
      )}
      style={n >= 4 ? { color: ATTENTION.gold } : undefined}
    >
      {Array.from({ length: 5 }, (_, i) => (
        <Star key={i} className={cn("h-2.5 w-2.5", i < n ? "fill-current" : "opacity-30")} />
      ))}
    </span>
  );
}
