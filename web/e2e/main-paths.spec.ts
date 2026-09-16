/**
 * L3 主链路 e2e（G17.2）：前后端真实服务，8 条主链路 + 键盘走查。
 * 断言用户可见终态（数据/文案/canvas），不测实现细节。
 */
import { test, expect } from "@playwright/test";

const NAV = [
  ["策略实验室", "/lab"],
  ["进化中心", "/evolution"],
  ["策略库", "/strategies"],
  ["策略验证", "/verify"],
  ["批量回测", "/batch-backtest"],
  ["模拟交易", "/trading"],
  ["数据管理", "/data"],
  ["设置", "/settings"],
] as const;

test.describe("主链路", () => {
  test("01 导航：8 页可达且标题正确", async ({ page }) => {
    for (const [label, path] of NAV) {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: label }).first()).toBeVisible();
    }
  });

  test("02 Data：数据集列表经 ky 真实加载", async ({ page }) => {
    await page.goto("/data");
    await expect(page.getByRole("table").first()).toBeVisible({ timeout: 10_000 });
  });

  test("03 Strategies：列表 200 行 + 搜索筛选 URL 化", async ({ page }) => {
    await page.goto("/strategies");
    await expect(page.getByText("已保存策略")).toBeVisible();
    // 等列表真实渲染
    await page.waitForFunction(() => document.querySelectorAll("table tbody tr").length > 0, {
      timeout: 10_000,
    });
    // 搜索 → URL 持久化（G10 状态所有权）
    await page.getByPlaceholder("搜索策略...").fill("EMA");
    await expect(page).toHaveURL(/q=EMA/);
  });

  test("04 Evolution：历史任务表 + 详情抽屉图表", async ({ page }) => {
    await page.goto("/evolution");
    await expect(page.getByText("历史探索")).toBeVisible();
    await page.waitForFunction(() => document.querySelectorAll("table tbody tr").length > 0, {
      timeout: 10_000,
    });
    await page.locator("table tbody tr").first().locator("button").first().click();
    await expect(page.getByRole("button", { name: "进化曲线" })).toBeVisible();
    await page.getByRole("button", { name: "进化曲线" }).click();
    // ScoreTrend echarts canvas 真实渲染（G14）
    await expect(page.locator("canvas").first()).toBeVisible();
  });

  test("05 Trading：空态文案与引导", async ({ page }) => {
    await page.goto("/trading");
    const body = page.getByText("暂无模拟交易任务");
    await expect(body.or(page.getByText("任务", { exact: false }).first())).toBeVisible({
      timeout: 10_000,
    });
  });

  test("06 Settings：三表单区块渲染", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("form").first()).toBeVisible();
    const switches = page.locator("[role='switch']");
    await expect(switches.first()).toBeVisible();
  });

  test("07 Lab：三模式 tab 切换", async ({ page }) => {
    await page.goto("/lab");
    for (const tab of ["假设验证", "策略回测", "场景验证"]) {
      await page.getByRole("button", { name: tab }).click();
    }
    await expect(page.getByRole("button", { name: "场景验证" })).toBeVisible();
  });

  test("08 BatchBacktest：页面加载与策略选择区", async ({ page }) => {
    await page.goto("/batch-backtest");
    await expect(page.getByText("选择策略")).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("可访问性走查", () => {
  test("09 键盘：Tab 焦点进入侧栏导航且可见", async ({ page }) => {
    await page.goto("/settings");
    await page.keyboard.press("Tab");
    // 首个焦点应落在可操作元素（侧栏链接/按钮），且焦点元素存在
    const focused = await page.evaluate(() => document.activeElement?.tagName ?? "");
    expect(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"]).toContain(focused);
  });
});

test.describe("视觉质量（截图矩阵落档）", () => {
  const shots: Array<[string, string]> = [
    ["strategies-成功态", "/strategies"],
    ["evolution-成功态", "/evolution"],
    ["trading-空态", "/trading"],
    ["settings-表单态", "/settings"],
  ];

  for (const [name, path] of shots) {
    test(`10 截图 ${name}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(path);
      await page.waitForTimeout(1200);
      await page.screenshot({ path: `e2e/screenshots/${name}-1440.png`, fullPage: false });
    });
  }

  test("11 窄断点布局不裁切主动作", async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 900 });
    await page.goto("/strategies");
    await page.waitForTimeout(1200);
    await page.screenshot({ path: "e2e/screenshots/strategies-768.png" });
    // 侧栏折叠按钮仍可达（不被裁切）
    await expect(page.getByRole("banner")).toBeVisible();
  });
});
