/**
 * 侧栏平铺形制锚（fe-shell-theme-cutover M1/M2/M3/M6 改写版）：
 * 平铺十项原序+三名回正（验证记录/模拟交易/插件与组件）+零分组标题负断言+
 * /lab 不入菜单负断言+实盘禁用维持+getPageTitle 三新名。
 * 折叠态 describe 随 M3 整体退役（useAppStore 侧栏字段已删）。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Sidebar, getPageTitle } from "@/components/layout/Sidebar";

const EXPECTED_ITEMS = [
  "观察工作台",
  "验证记录",
  "批量回测",
  "进化中心",
  "策略库",
  "模拟交易",
  "实盘交易",
  "数据管理",
  "设置",
  "插件与组件",
] as const;

const RETIRED_NAMES = [
  "策略验证",
  "纸盘交易", // M2 退役名（菜单现名「模拟交易」——fe-asset-data-domain 2.2
  // 术语清零只清呈现面，退役名负断言清单保留旧名本体防回退）
  "插件与组件库",
  "策略实验室",
] as const;

const RETIRED_GROUP_TITLES = [
  "研究验证",
  "策略资产",
  "执行监控",
  "数据系统",
] as const;

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar 平铺形制（M1）", () => {
  it("nav 内恰 10 项（9 链接+1 禁用 span——实盘 M5），顺序逐项对照原型", () => {
    renderSidebar();
    const nav = screen.getByRole("navigation", { name: "主导航" });
    const items = Array.from(
      nav.querySelectorAll("a, [aria-disabled='true']"),
    );
    expect(items).toHaveLength(10);
    expect(items.map((l) => l.textContent)).toEqual([...EXPECTED_ITEMS]);
  });

  it("旧分组标题零残留（负断言）", () => {
    renderSidebar();
    for (const title of RETIRED_GROUP_TITLES) {
      expect(screen.queryByText(title)).toBeNull();
    }
  });

  it("旧命名零残留（负断言——M2 三名回正）", () => {
    renderSidebar();
    for (const name of RETIRED_NAMES) {
      expect(screen.queryByText(name)).toBeNull();
    }
  });

  it("/lab 不入菜单（M6——负断言）", () => {
    renderSidebar();
    const labLink = screen.queryByRole("link", { name: /策略实验室/ });
    expect(labLink).toBeNull();
  });

  it("实盘交易维持禁用（M5——aria-disabled 不可点）", () => {
    renderSidebar();
    const disabled = screen
      .getByText("实盘交易")
      .closest('[aria-disabled="true"]');
    expect(disabled).not.toBeNull();
  });
});

describe("getPageTitle 三新名（M2）", () => {
  it("三个改名路由的页题与菜单名逐字一致", () => {
    expect(getPageTitle("/verify")).toBe("验证记录");
    expect(getPageTitle("/trading")).toBe("模拟交易");
    expect(getPageTitle("/plugins")).toBe("插件与组件");
  });

  it("未改路由页题不变", () => {
    expect(getPageTitle("/workbench")).toBe("观察工作台");
    expect(getPageTitle("/lab")).toBe("策略实验室");
  });
});
