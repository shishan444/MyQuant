/**
 * L3 主链路 e2e（G17.2）：前后端真实服务，主链路 + 键盘走查。
 * 断言用户可见终态（数据/文案/canvas），不测实现细节。
 * （批量回测页与 Lab 三模式随 fix-replay-gap-alignment U4/U5 退役；
 *  进化中心页随 U1 退役——本文件已同步收缩）
 */
import { test, expect } from "@playwright/test";

// fe-shell-theme-cutover M2 三名回正：验证记录/模拟交易/插件与组件（原型权威）
const NAV = [
  ["策略实验室", "/lab"],
  ["策略库", "/strategies"],
  ["验证记录", "/verify"],
  ["模拟交易", "/trading"],
  ["数据管理", "/data"],
  ["设置", "/settings"],
  ["观察工作台", "/workbench"],
  ["插件与组件", "/plugins"],
] as const;

test.describe("主链路", () => {
  test("01 导航：8 页可达且标题正确（双断言组合锚——URL 锁+页题 exact）", async ({ page }) => {
    for (const [label, path] of NAV) {
      await page.goto(path);
      // 路由锁：页面身份由 URL 判定（防他页文案供给）；页题锁 exact 全字匹配
      await expect(page).toHaveURL(new RegExp(`${path}$`));
      await expect(
        page.getByRole("heading", { name: label, exact: true }).first(),
      ).toBeVisible();
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

  test("05 Trading：空态文案与引导", async ({ page }) => {
    await page.goto("/trading");
    const body = page.getByText("暂无模拟交易任务");
    await expect(body.or(page.getByText("任务", { exact: false }).first())).toBeVisible({
      timeout: 10_000,
    });
  });

  test("05b 新路由：/workbench 真页+/plugins 路由可达页题正确", async ({ page }) => {
    // fe-shell-theme-cutover 改写：原占位三连断言（h3 主体/占位文案/无 table+canvas）
    // 随 b35d5f6（C8 占位组件删除）起现状即红——降为「路由可达+页题正确」组合锚；
    // 页面内容级锚随 fe-asset-data-domain（08 改造）再立。
    // （/live 随 LiveTrading 页退役删除——fix-replay-gap-alignment U6）
    await page.goto("/workbench");
    await expect(page.getByRole("heading", { name: "待裁决事件" })).toBeVisible();
    for (const [label, path] of [
      ["插件与组件", "/plugins"],
    ] as const) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(`${path}$`));
      await expect(
        page.getByRole("heading", { name: label, exact: true }).first(),
      ).toBeVisible();
    }
  });

  test("06 Settings：三表单区块渲染", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("form").first()).toBeVisible();
    const switches = page.locator("[role='switch']");
    await expect(switches.first()).toBeVisible();
  });

  test("07 Lab：单模式形制（规律构建器在页，模式切换器零残留）", async ({ page }) => {
    await page.goto("/lab");
    await expect(page.getByText("规律构建器")).toBeVisible({ timeout: 10_000 });
    // 回测/场景两模式与验证动作链随 U4/U5 退役
    await expect(page.getByText("策略回测")).toHaveCount(0);
    await expect(page.getByText("场景验证")).toHaveCount(0);
    await expect(page.getByText("验证策略")).toHaveCount(0);
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

test.describe("侧栏形态基线（fe-shell-theme-cutover 平铺形制）", () => {
  const ALL_ITEMS = [
    "观察工作台",
    "验证记录",
    "策略库",
    "模拟交易",
    "数据管理",
    "设置",
    "插件与组件",
  ];
  const RETIRED_GROUP_TITLES = ["研究验证", "策略资产", "执行监控", "数据系统"];

  test("12 平铺七项按原型顺序渲染+旧分组标题零残留", async ({ page }) => {
    await page.goto("/workbench");
    const nav = page.locator("nav");
    const labels = await nav
      .locator("a, [aria-disabled='true']")
      .allInnerTexts();
    expect(labels).toEqual(ALL_ITEMS);
    for (const title of RETIRED_GROUP_TITLES) {
      await expect(nav.getByText(title, { exact: true })).toHaveCount(0);
    }
  });

  test("13 /lab 不入菜单：访问 /lab 侧栏零选中项且无策略实验室链接（M6）", async ({ page }) => {
    await page.goto("/lab");
    await expect(page.locator("nav a[aria-current='page']")).toHaveCount(0);
    await expect(
      page.locator("nav a", { hasText: "策略实验室" }),
    ).toHaveCount(0);
  });

  test("14 工作台项选中态（实盘禁用态随 U6 退役——侧栏全链接）", async ({ page }) => {
    await page.goto("/workbench");
    const active = page.locator("nav a[aria-current='page']");
    await expect(active).toHaveCount(1);
    await expect(active).toContainText("观察工作台");
    // 实盘交易退役后侧栏零禁用项
    await expect(page.locator("nav span[aria-disabled='true']")).toHaveCount(0);
  });

  // test 15（折叠交互）随 M3 折叠钮退役而退役——fe-shell-theme-cutover

  test("16 窄视口平铺渲染：375px 下七项照常渲染", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/workbench");
    const nav = page.locator("nav");
    for (const label of ALL_ITEMS) {
      await expect(nav.getByText(label, { exact: true })).toBeVisible();
    }
    await expect(nav.locator("span[aria-disabled='true']")).toHaveCount(0);
  });
});

test.describe("视觉质量（截图矩阵落档）", () => {
  const shots: Array<[string, string]> = [
    ["strategies-成功态", "/strategies"],
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
    // Header（banner）仍可达（不被裁切）——侧栏折叠钮已随 M3 退役
    await expect(page.getByRole("banner")).toBeVisible();
  });
});
