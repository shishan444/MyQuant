/**
 * Sidebar 结构单测（global-nav-shell 任务 3.4 + 审查期特征网补齐）：
 * 四组 10 项目标信息架构+02 无高亮位规则+08 禁用态（含键盘不可达）
 * +折叠态形制（分组标题隐藏/无高亮位在折叠态的表现）。
 * 特征网语义：断言均为现状记录，不预设应然行为。
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router";
import { Sidebar, getPageTitle } from "@/components/layout/Sidebar";
import { useAppStore } from "@/stores/app";

function renderSidebarAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar />
      <Routes>
        <Route path="*" element={<div>page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const GROUP_TITLES = ["研究验证", "策略资产", "执行监控", "数据系统"];
const ALL_ITEMS = [
  "观察工作台",
  "策略验证",
  "批量回测",
  "进化中心",
  "插件与组件库",
  "策略库",
  "纸盘交易",
  "实盘交易",
  "数据管理",
  "设置",
];

describe("Sidebar（G-2 四组信息架构）", () => {
  it("渲染四组分组标题+全部 10 项", () => {
    renderSidebarAt("/workbench");
    for (const title of GROUP_TITLES) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    for (const label of ALL_ITEMS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("置顶组无分组标题：分组标题恰好四组（工作台独立置顶形制）", () => {
    renderSidebarAt("/workbench");
    // 分组标题的形制类为 uppercase tracking-wider（Sidebar.tsx 分组标题 div）
    const titles = document.querySelectorAll("nav div.uppercase");
    expect(titles).toHaveLength(4);
    // 置顶组：观察工作台项之前无任何分组标题元素
    const workbench = screen.getByText("观察工作台");
    expect(workbench.closest("div")?.querySelector("div.uppercase")).toBeNull();
  });

  it("02 无高亮位规则：访问 /lab 时侧栏零 active 项", () => {
    renderSidebarAt("/lab");
    const active = document.querySelector("a[aria-current='page']");
    expect(active).toBeNull();
    // 侧栏不含 02 假设工作台项（随 C4 落地补入）
    expect(screen.queryByText("假设工作台")).toBeNull();
  });

  it("工作台项选中态（aria-current）", () => {
    renderSidebarAt("/workbench");
    const active = document.querySelector("a[aria-current='page']");
    expect(active).not.toBeNull();
    expect(active?.textContent).toContain("观察工作台");
  });

  it("08 实盘交易为禁用态：无链接语义+aria-disabled+禁用色", () => {
    renderSidebarAt("/workbench");
    const item = screen.getByText("实盘交易").closest("span[aria-disabled='true']");
    expect(item).not.toBeNull();
    expect(item?.querySelector("a")).toBeNull();
    // 禁用形制：禁用色+不可点击光标（现状记录）
    expect(item?.className).toContain("text-text-muted");
    expect(item?.className).toContain("cursor-not-allowed");
  });

  it("getPageTitle 覆盖新路由与改名（07 纸盘交易）", () => {
    expect(getPageTitle("/workbench")).toBe("观察工作台");
    expect(getPageTitle("/live")).toBe("实盘交易");
    expect(getPageTitle("/plugins")).toBe("插件与组件库");
    expect(getPageTitle("/trading")).toBe("纸盘交易");
    expect(getPageTitle("/lab")).toBe("策略实验室");
  });

  it("getPageTitle 未知路径回退 MyQuant（边界）", () => {
    expect(getPageTitle("/nonexistent-route")).toBe("MyQuant");
    expect(getPageTitle("/")).toBe("MyQuant");
  });
});

describe("Sidebar 键盘可达性（特征网）", () => {
  it("08 实盘项键盘不可达：Tab 序列不含禁用项，正常项可达（对照锚）", async () => {
    const user = userEvent.setup();
    renderSidebarAt("/workbench");
    const disabledNode = screen
      .getByText("实盘交易")
      .closest("span[aria-disabled='true']");
    expect(disabledNode).not.toBeNull();
    // 无 tabindex 属性（span 天然不可聚焦，实现未添加任何焦点钩子）
    expect(disabledNode?.getAttribute("tabindex")).toBeNull();

    // 展开态侧栏可聚焦元素：9 条 NavLink+1 个折叠按钮——遍历一圈收集焦点序列
    const focused: (Element | null)[] = [];
    for (let i = 0; i < 14; i++) {
      await user.tab();
      focused.push(document.activeElement);
    }
    expect(focused).not.toContain(disabledNode);
    // 对照锚：正常链接在 Tab 序列中可达，证明遍历有效
    const workbenchLink = screen.getByText("观察工作台").closest("a");
    expect(focused).toContain(workbenchLink);
  });
});

describe("Sidebar 折叠态形制（特征网）", () => {
  beforeEach(() => {
    useAppStore.setState({ sidebarCollapsed: false });
  });

  function renderCollapsedAt(path: string) {
    useAppStore.setState({ sidebarCollapsed: true });
    return renderSidebarAt(path);
  }

  it("折叠态：分组标题与文字标签全部隐藏，链接 9 条+禁用 span 1 条", () => {
    renderCollapsedAt("/workbench");
    for (const title of GROUP_TITLES) {
      expect(screen.queryByText(title)).toBeNull();
    }
    for (const label of ALL_ITEMS) {
      expect(screen.queryByText(label)).toBeNull();
    }
    // 10 项中 9 项为 NavLink（a），08 实盘为禁用 span
    expect(document.querySelectorAll("nav a")).toHaveLength(9);
    const disabled = document.querySelectorAll("nav span[aria-disabled='true']");
    expect(disabled).toHaveLength(1);
    // Logo 文字与版本号随折叠隐藏
    expect(screen.queryByText("MyQuant")).toBeNull();
    expect(screen.queryByText("v1.0.0")).toBeNull();
  });

  it("02 无高亮位在折叠态的表现：访问 /lab 折叠侧栏零 active 项", () => {
    renderCollapsedAt("/lab");
    expect(document.querySelector("a[aria-current='page']")).toBeNull();
  });

  it("折叠态 active 形制（对照锚）：/workbench 唯一选中且带金色选中类", () => {
    renderCollapsedAt("/workbench");
    const active = document.querySelector("a[aria-current='page']");
    expect(active).not.toBeNull();
    expect(active?.className).toContain("bg-accent-gold/10");
    expect(active?.className).toContain("border-accent-gold");
    // 全侧栏唯一 active
    expect(document.querySelectorAll("a[aria-current='page']")).toHaveLength(1);
  });
});
