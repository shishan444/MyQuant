import { Tooltip } from "antd";
import { NavLink, useLocation } from "react-router";
import {
  FlaskConical,
  Dna,
  BookOpen,
  ShieldCheck,
  LineChart,
  TrendingUp,
  Database,
  Settings,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app";


const NAV_ITEMS = [
  { to: "/lab", icon: FlaskConical, label: "策略实验室" },
  { to: "/evolution", icon: Dna, label: "进化中心" },
  { to: "/strategies", icon: BookOpen, label: "策略库" },
  { to: "/verify", icon: ShieldCheck, label: "策略验证" },
  { to: "/batch-backtest", icon: LineChart, label: "批量回测" },
  { to: "/trading", icon: TrendingUp, label: "模拟交易" },
  { to: "/data", icon: Database, label: "数据管理" },
  { to: "/settings", icon: Settings, label: "设置" },
] as const;

const PAGE_TITLES: Record<string, string> = {
  "/lab": "策略实验室",
  "/evolution": "进化中心",
  "/strategies": "策略库",
  "/verify": "策略验证",
  "/batch-backtest": "批量回测",
  "/trading": "模拟交易",
  "/data": "数据管理",
  "/settings": "设置",
};

export function getPageTitle(pathname: string): string {
  return PAGE_TITLES[pathname] || "MyQuant";
}

export function Sidebar() {
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);

  return (
    <>
      <aside
        className={cn(
          "flex h-screen flex-col border-r border-border-default bg-bg-surface backdrop-blur-md transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]",
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

        {/* Navigation */}
        <nav className="flex-1 py-2">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) =>
            collapsed ? (
              <Tooltip key={to} placement="right" mouseEnterDelay={0} title={label}>
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
                    <Icon className="h-5 w-5" />
                  </NavLink>
              </Tooltip>
            ) : (
              <NavLink
                key={to}
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
                <Icon className="h-5 w-5 shrink-0" />
                <span className="text-sm font-medium truncate">{label}</span>
              </NavLink>
            )
          )}
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
