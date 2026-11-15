/**
 * L3 悬停浮层 e2e（fix-chart-tooltip-restore 组 5，Q3 裁决 2026-11-14 入必做）。
 *
 * 数据真实性终审层：真实服务+真实数据+真实 canvas+真实鼠标移动（零 mock）——
 * G-1 Equity 悬停金额值域锁正则+浮层 boundingBox 完整含于图表容器（R-F
 * 「不出容器边界」行为终审；真布局下 measure 非 0，宽高传反/返回值未用于
 * style 类 jsdom 不可判别缺陷在此可拦）；G-2 时间行形态；G-3 离图消失；
 * G-4 ScoreTrend 悬停头部在场。web/e2e 首例 mouse.move 悬停用例。
 *
 * 金额验收正则（change 工件 tasks.md 组 5 唯一持久权威副本，三方定稿）：
 * /^\$-?(\d+\.\d{2}M|\d+\.\dK|1000|\d{1,3})$/
 */
import { test, expect } from "@playwright/test";

// 值域锁正则：14 真实产物全匹配/11 非产物全拒绝（判档锚在单测层承载）
const MONEY_RE = /^\$-?(\d+\.\d{2}M|\d+\.\dK|1000|\d{1,3})$/;
// 时间行 MM-DD HH:mm
const TIME_RE = /^\d{2}-\d{2} \d{2}:\d{2}$/;

test.describe("图表悬停浮层（CARD-01 方向 A）", () => {
  test("G-1~G-3 Equity：真实回测资金曲线悬停+值域锁+容器内+离图消失", async ({ page }) => {
    test.setTimeout(180_000);
    // 真实链路：lab 策略回测模式 → 策略库选 BTCUSDT 4h 真实策略 → 真实回测
    await page.goto("/lab");
    await page.getByRole("button", { name: "策略回测" }).click();
    // 策略选择器（antd Select，aria-label 锚）→ 选 BTCUSDT 4h 项：
    // 限定 .ant-select-dropdown 浮层内的可见 option（antd 下拉动画期裸
    // getByRole 定位到不稳定节点，342 次 not visible 实录）
    const selector = page.getByLabel("从策略库选择");
    await selector.click();
    const dropdown = page.locator(".ant-select-dropdown:visible").first();
    await expect(dropdown).toBeVisible({ timeout: 5_000 });
    // 键盘选择第二项（首项 E2ECL 策略的数据集已随闭环 e2e 清理，选
    // BTCUSDT 4h）：下拉面板在数据刷新时重挂载，DOM 点击持续 not
    // stable/not visible（双实录）；键盘事件作用于 Select 本体不受影响
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    // 选中态断言锚「策略装载完成」面：策略基因摘要渲染（执行: BTCUSDT / 4H）
    //——readonly input 文本恒空、本 antd 版回显元素无 .ant-select-selection-item
    // class，均不可锚；装载完成是回测可运行的前置判据
    await expect(page.getByText(/执行: BTCUSDT \/ 4H/)).toBeVisible({ timeout: 5_000 });
    // 运行真实回测（BTCUSDT 4h 全量 5954 根，后端引擎跑完）
    await page.getByRole("button", { name: "运行回测" }).click();
    // 资金曲线区块出现（equityPoints>0 才渲染，等真实结果）
    const chartContainer = page.getByTestId("equity-chart-container");
    await expect(chartContainer).toBeVisible({ timeout: 120_000 });

    // G-1：mouse.move 至图表区中心 → 浮层出现（真实 canvas 事件链）。
    // 先滚入视口：boundingBox 是文档坐标而 mouse 事件是视口坐标，页面
    // 滚动时中心点错位出容器（浮层不现实录）
    await chartContainer.scrollIntoViewIfNeeded();
    const box = await chartContainer.boundingBox();
    expect(box).not.toBeNull();
    const cx = box!.x + box!.width / 2;
    const cy = box!.y + box!.height / 2;
    await page.mouse.move(cx, cy);
    const tooltip = page.getByTestId("equity-tooltip");
    await expect(tooltip).toBeVisible({ timeout: 5_000 });

    // 金额值域锁：第二行 `Equity: $...` 金额部分匹配终稿正则
    const line2 = tooltip.locator("div").nth(1);
    const moneyText = (await line2.textContent()) ?? "";
    const money = moneyText.replace(/^Equity:\s*/, "");
    expect(moneyText).toMatch(/^Equity: /);
    expect(money).toMatch(MONEY_RE);

    // G-2：时间行形态 MM-DD HH:mm
    const line1 = tooltip.locator("div").nth(0);
    expect(((await line1.textContent()) ?? "").trim()).toMatch(TIME_RE);

    // G-1 续：浮层 boundingBox 完整含于图表容器（R-F 行为终审）
    const tipBox = await tooltip.boundingBox();
    expect(tipBox).not.toBeNull();
    expect(tipBox!.x).toBeGreaterThanOrEqual(box!.x);
    expect(tipBox!.y).toBeGreaterThanOrEqual(box!.y);
    expect(tipBox!.x + tipBox!.width).toBeLessThanOrEqual(box!.x + box!.width + 0.5);
    expect(tipBox!.y + tipBox!.height).toBeLessThanOrEqual(box!.y + box!.height + 0.5);

    // G-3：mouse.move 移出图表容器 → 浮层不可见
    await page.mouse.move(box!.x - 20, box!.y - 20);
    await expect(tooltip).toBeHidden({ timeout: 5_000 });
  });

  test("G-4 ScoreTrend：进化曲线悬停头部在场（第 N 代）", async ({ page }) => {
    test.setTimeout(120_000);
    // main-paths 04 同链路：历史任务列表 → 行展开 → 进化曲线
    await page.goto("/evolution");
    await expect(page.getByText("历史任务")).toBeVisible();
    await page.waitForFunction(
      () => document.querySelectorAll("table tbody tr").length > 0,
      { timeout: 15_000 },
    );
    await page.locator("table tbody tr").first().locator("button").first().click();
    const curveBtn = page.getByRole("button", { name: "进化曲线" });
    await expect(curveBtn).toBeVisible();
    await curveBtn.click();

    // 悬停图表区中心 → 浮层头部「第 N 代」在场（真实代记录反查）
    const container = page.getByTestId("score-chart-container");
    await expect(container).toBeVisible({ timeout: 15_000 });
    await container.scrollIntoViewIfNeeded();
    const box = await container.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    const tooltip = page.getByTestId("score-tooltip");
    await expect(tooltip).toBeVisible({ timeout: 5_000 });
    await expect(tooltip.getByText(/^第 \d+ 代$/)).toBeVisible();
  });
});
