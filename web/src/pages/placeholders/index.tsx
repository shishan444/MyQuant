import { LayoutDashboard, Activity } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";

/**
 * global-nav-shell 路由占位（诚实占位，不画假内容——web-ui-truthfulness）。
 * 各页面在后续 change 建成时替换为真实页面组件。
 * （/plugins 已于 alignment-batch D6 换为真实 Plugins 页）
 */

export function WorkbenchPlaceholder() {
  return (
    <EmptyState
      icon={LayoutDashboard}
      title="观察工作台"
      description="本页面将在后续版本建设"
    />
  );
}

export function LivePlaceholder() {
  return (
    <EmptyState
      icon={Activity}
      title="实盘交易"
      description="本页面将在后续版本建设"
    />
  );
}
