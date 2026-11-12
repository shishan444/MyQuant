import { Tooltip } from "antd";
import { NavLink, useLocation } from "react-router";
import {
  Dna,
  ShieldCheck,
  Layers,
  Brain,
  TrendingUp,
  Database,
  Settings,
  LayoutDashboard,
  Puzzle,
  Radio,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 全局导航壳层（global-nav-shell）——职能=平铺十项全局导航，
 * 导出 Sidebar（左侧导航）/Header（页头）/getPageTitle（路由→页题，供 Header 消费）。
 * 服务对象=AppLayout（AppLayout.tsx:2-3 引入 Sidebar 与 Header），由其组合 Sidebar 导航与 Header 页头。
 * 边界=不承担页面内容渲染（页面内容由 AppLayout 的 Outlet 挂载）；
 * /lab（策略实验室）保留路由不入菜单（fe-shell-theme-cutover M6 定位）。
 */
interface NavItem {
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  /** 禁用态：无链接语义、不可聚焦（08 实盘交易建成前禁用——原型决策记录 1） */
  disabled?: boolean;
}

/**
 * 平铺 10 项目标信息架构（前端原型还原 ①：四组分组与折叠钮退役，原型平铺权威）。
 */
const NAV_ITEMS: NavItem[] = [
  { to: "/workbench", icon: LayoutDashboard, label: "观察工作台" },
  { to: "/verify", icon: ShieldCheck, label: "验证记录" },
  { to: "/batch-backtest", icon: Layers, label: "批量回测" },
  { to: "/evolution", icon: Dna, label: "进化中心" },
  { to: "/strategies", icon: Brain, label: "策略库" },
  { to: "/trading", icon: TrendingUp, label: "模拟交易" },
  { to: "/live", icon: Radio, label: "实盘交易", disabled: true },
  { to: "/data", icon: Database, label: "数据管理" },
  { to: "/settings", icon: Settings, label: "设置" },
  { to: "/plugins", icon: Puzzle, label: "插件与组件" },
];

const PAGE_TITLES: Record<string, string> = {
  "/workbench": "观察工作台",
  "/lab": "策略实验室",
  "/evolution": "进化中心",
  "/strategies": "策略库",
  "/verify": "验证记录",
  "/batch-backtest": "批量回测",
  "/trading": "模拟交易",
  "/data": "数据管理",
  "/settings": "设置",
  "/live": "实盘交易",
  "/plugins": "插件与组件",
};

export function getPageTitle(pathname: string): string {
  return PAGE_TITLES[pathname] || "MyQuant";
}

function NavItemLink({ item }: { item: NavItem }) {
  const { to, icon: Icon, label, disabled } = item;

  const content = (
    <>
      <Icon className="h-5 w-5 shrink-0" />
      <span className="text-sm font-medium truncate">{label}</span>
    </>
  );

  if (disabled) {
    return (
      <Tooltip placement="right" mouseEnterDelay={0} title={`${label}（建设中）`}>
        <span
          aria-disabled="true"
          className="flex h-12 items-center gap-3 px-4 transition-colors cursor-not-allowed text-text-muted border-l-2 border-transparent"
        >
          {content}
        </span>
      </Tooltip>
    );
  }

  return (
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
  return (
    <aside className="flex h-screen w-48 flex-col border-r border-border-default bg-bg-surface backdrop-blur-md">
      {/* Logo 区：品牌标识位 */}
      <div className="flex h-12 items-center border-b border-border-default px-4">
        <span className="text-lg font-semibold text-accent-gold font-mono tracking-tight">
          MyQuant
        </span>
      </div>

      {/* Navigation：平铺十项（fe-shell-theme-cutover M1） */}
      <nav className="flex-1 overflow-y-auto py-2" aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <NavItemLink key={item.to} item={item} />
        ))}
      </nav>

      <div className="border-t border-border-default p-2">
        <p className="text-center text-xs text-text-muted">v1.0.0</p>
      </div>
    </aside>
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
