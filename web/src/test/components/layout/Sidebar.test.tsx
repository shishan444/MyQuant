/**
 * Sidebar 结构单测（global-nav-shell 任务 3.4）：
 * 四组 10 项目标信息架构+02 无高亮位规则+08 禁用态。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { Sidebar, getPageTitle } from "@/components/layout/Sidebar";

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

  it("08 实盘交易为禁用态：无链接语义+aria-disabled", () => {
    renderSidebarAt("/workbench");
    const item = screen.getByText("实盘交易").closest("span[aria-disabled='true']");
    expect(item).not.toBeNull();
    expect(item?.querySelector("a")).toBeNull();
  });

  it("getPageTitle 覆盖新路由与改名（07 纸盘交易）", () => {
    expect(getPageTitle("/workbench")).toBe("观察工作台");
    expect(getPageTitle("/live")).toBe("实盘交易");
    expect(getPageTitle("/plugins")).toBe("插件与组件库");
    expect(getPageTitle("/trading")).toBe("纸盘交易");
    expect(getPageTitle("/lab")).toBe("策略实验室");
  });
});
