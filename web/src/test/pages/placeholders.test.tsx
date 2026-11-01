/**
 * global-nav-shell 三条新路由占位组件单测（L1 契约单元层，特征网）：
 * EmptyState 诚实占位（标题+建设说明）+无模拟数据形制（负面断言）。
 * 契约：openspec/changes/global-nav-shell Scenario「未建页面诚实占位」。
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  WorkbenchPlaceholder,
  LivePlaceholder,
  PluginsPlaceholder,
} from "@/pages/placeholders";

const CASES = [
  ["WorkbenchPlaceholder", WorkbenchPlaceholder, "观察工作台"],
  ["LivePlaceholder", LivePlaceholder, "实盘交易"],
  ["PluginsPlaceholder", PluginsPlaceholder, "插件与组件库"],
] as const;

describe("新路由占位组件（诚实占位）", () => {
  it.each(CASES)("%s 渲染 EmptyState：h3 标题+建设说明文案", (_name, Comp, title) => {
    const { container } = render(<Comp />);
    expect(screen.getByRole("heading", { level: 3, name: title })).toBeInTheDocument();
    expect(screen.getByText("本页面将在后续版本建设")).toBeInTheDocument();
    // 诚实占位负面断言：不呈现表格/图表等模拟数据形制
    expect(screen.queryByRole("table")).toBeNull();
    expect(container.querySelector("canvas")).toBeNull();
  });
});
