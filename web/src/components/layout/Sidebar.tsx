import { Tooltip } from "antd";
import { NavLink, useLocation } from "react-router";
import {
  Dna,
  BookOpen,
  ShieldCheck,
  LineChart,
  TrendingUp,
  Database,
  Settings,
  LayoutDashboard,
  Puzzle,
  Activity,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app";

interface NavItem {
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  /** 禁用态：无链接语义、不可聚焦（08 实盘交易建成前禁用——原型决策记录 1） */
  disabled?: boolean;
}

interface NavGroup {
  /** null = 置顶组（工作台独立置顶，无分组标题） */
  title: string | null;
  items: NavItem[];
}

/**
 * 四组 10 项目标信息架构（global-nav-shell）：
 * 工作台置顶 + 研究验证 / 策略资产 / 执行监控 / 数据系统。
 * 02 假设工作台项缺位——/lab（旧策略实验室）随 C4 重构落地时补入研究验证组首位，
 * 在此之前访问 /lab 时侧栏零高亮。
 */
const NAV_GROUPS: NavGroup[] = [
  {
    title: null,
    items: [{ to: "/workbench", icon: LayoutDashboard, label: "观察工作台" }],
  },
  {
    title: "研究验证",
    items: [
      { to: "/verify", icon: ShieldCheck, label: "策略验证" },
      { to: "/batch-backtest", icon: LineChart, label: "批量回测" },
      { to: "/evolution", icon: Dna, label: "进化中心" },
      { to: "/plugins", icon: Puzzle, label: "插件与组件库" },
    ],
  },
  {
    title: "策略资产",
    items: [{ to: "/strategies", icon: BookOpen, label: "策略库" }],
  },
  {
    title: "执行监控",
    items: [
      { to: "/trading", icon: TrendingUp, label: "纸盘交易" },
      { to: "/live", icon: Activity, label: "实盘交易", disabled: true },
    ],
  },
  {
    title: "数据系统",
    items: [
      { to: "/data", icon: Database, label: "数据管理" },
      { to: "/settings", icon: Settings, label: "设置" },
    ],
  },
];

const PAGE_TITLES: Record<string, string> = {
  "/workbench": "观察工作台",
  "/lab": "策略实验室",
  "/evolution": "进化中心",
  "/strategies": "策略库",
  "/verify": "策略验证",
  "/batch-backtest": "批量回测",
  "/trading": "纸盘交易",
  "/data": "数据管理",
  "/settings": "设置",
  "/live": "实盘交易",
  "/plugins": "插件与组件库",
};

export function getPageTitle(pathname: string): string {
  return PAGE_TITLES[pathname] || "MyQuant";
}

function NavItemLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { to, icon: Icon, label, disabled } = item;

  const content = collapsed ? (
    <Icon className="h-5 w-5" />
  ) : (
    <>
      <Icon className="h-5 w-5 shrink-0" />
      <span className="text-sm font-medium truncate">{label}</span>
    </>
  );

  if (disabled) {
    const disabledNode = (
      <span
        aria-disabled="true"
        className={cn(
          "flex h-12 items-center transition-colors cursor-not-allowed text-text-muted border-l-2 border-transparent",
          collapsed ? "justify-center" : "gap-3 px-4"
        )}
      >
        {content}
      </span>
    );
    return collapsed ? (
      <Tooltip placement="right" mouseEnterDelay={0} title={`${label}（建设中）`}>
        {disabledNode}
      </Tooltip>
    ) : (
      disabledNode
    );
  }

  return collapsed ? (
    <Tooltip placement="right" mouseEnterDelay={0} title={label}>
      <NavLink
        to={to}
        className={({ isActive }) =>
          cn(
            "flex h-12 items-center justify-center transition-colors",
            isActive
              ? "bg-accent-gold/10 text-accent-gold border-l-2 border-accent-gold"
              : "text-text-secondary hover:text-text-primary hover:bg-white/5 border-l-2 border-transparent"
          )
        }
      >
        {content}
      </NavLink>
    </Tooltip>
  ) : (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex h-12 items-center gap-3 px-4 transition-colors",
          isActive
            ? "bg-accent-gold/10 text-accent-gold border-l-2 border-accent-gold"
            : "text-text-secondary hover:text-text-primary hover:bg-white/5 border-l-2 border-transparent"
        )
      }
    >
      {content}
    </NavLink>
  );
}

export function Sidebar() {
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);

  return (
    <>
      <aside
        className={cn(
          "flex h-screen flex-col border-r border-border-default bg-bg-surface backdrop-blur-md transition-all duration-300 ease-[cubic-bezier(0.4,0.0,0.2,1)]",
          collapsed ? "w-16" : "w-60"
        )}
      >
        {/* Logo */}
        <div className="flex h-12 items-center border-b border-border-default px-4">
          {!collapsed && (
            <span className="text-lg font-semibold text-accent-gold font-mono tracking-tight">
              MyQuant
            </span>
          )}
        </div>

        {/* Navigation：四组分组（折叠态隐藏分组标题） */}
        <nav className="flex-1 overflow-y-auto py-2" aria-label="主导航">
          {NAV_GROUPS.map((group, groupIndex) => (
            <div key={group.title ?? `top-${groupIndex}`}>
              {!collapsed && group.title !== null && (
                <div className="px-4 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
                  {group.title}
                </div>
              )}
              {group.items.map((item) => (
                <NavItemLink key={item.to} item={item} collapsed={collapsed} />
              ))}
            </div>
          ))}
        </nav>

        {/* Collapse button */}
        <div className="border-t border-border-default p-2">
          <button
            onClick={toggleSidebar}
            className="flex h-10 w-full items-center justify-center rounded-lg text-text-secondary hover:text-text-primary hover:bg-white/5 transition-colors"
          >
            {collapsed ? (
              <ChevronsRight className="h-5 w-5" />
            ) : (
              <ChevronsLeft className="h-5 w-5" />
            )}
          </button>
          {!collapsed && (
            <p className="mt-1 text-center text-xs text-text-muted">
              v1.0.0
            </p>
          )}
        </div>
      </aside>
    </>
  );
}

export function Header() {
  const { pathname } = useLocation();
  const title = getPageTitle(pathname);

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border-default bg-bg-surface/90 px-6 backdrop-blur-md">
      <h1 className="text-sm font-medium text-text-secondary">{title}</h1>
      <div className="flex items-center gap-3">
        <button className="flex h-8 w-8 items-center justify-center rounded-lg text-text-secondary hover:text-text-primary hover:bg-white/5 transition-colors">
          <TrendingUp className="h-4 w-4" />
        </button>
        <button className="flex h-8 w-8 items-center justify-center rounded-lg text-text-secondary hover:text-text-primary hover:bg-white/5 transition-colors">
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
