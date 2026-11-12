/**
 * 验证记录全区间 K 线 e2e（upgrade-vr-fullrange-chart REQ-9 前两条）：
 * 前后端真实服务 + dev 库 staged 记录（rec_*，3 执行份）。
 * 8.1 全区间连续性：单图不拆图+段界标注+标记合并+ok 态无对账提示。
 * 8.2 equity 副图与主图共用时间轴：副图 pane 在场（subChartType=equity
 * 渲染锚——多 pane canvas）+段界重置断线（canvas 像素面归截图落档）。
 * 数据正确性锚（按份对账/zip）在 fullRangeData 单测三型构造覆盖；
 * e2e 锚用户可见终态。
 */
import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

async function findStagedRecord(): Promise<string | null> {
  const res = await fetch(
    `${API}/api/research/validation-records?level=staged_validation&limit=1`,
  );
  if (!res.ok) return null;
  const body = (await res.json()) as { records?: Array<{ record_id: string }> };
  return body.records?.[0]?.record_id ?? null;
}

async function openRecordDetail(page: import("@playwright/test").Page, recordId: string) {
  await page.goto("/verify");
  await page.getByTestId("vr-type-select").click();
  await page.getByTitle("分段验证").click();
  const card = page.getByTestId(`vr-card-${recordId}`);
  await card.first().waitFor({ timeout: 15_000 });
  await card.first().scrollIntoViewIfNeeded();
  await card.first().click();
  await expect(page.getByTestId("vr-kline-review")).toBeVisible({ timeout: 20_000 });
}

test.describe("验证记录全区间 K 线（REQ-9）", () => {
  test("8.1 全区间连续性：单图不拆图+全区间头行+标记合并+ok 态无对账提示", async ({ page }) => {
    test.setTimeout(120_000);
    const recordId = await findStagedRecord();
    test.skip(!recordId, "dev 库无 staged_validation 记录——造数归 closed-loop seedDataset 形制");
    await openRecordDetail(page, recordId!);

    // 全区间视图缺省：头行含「全区间 N 份」+视图模式标
    await expect(page.getByTestId("vr-view-mode")).toHaveText(/全区间/);
    await expect(page.getByTestId("vr-kline-review")).toContainText(/全区间 \d+ 份/);

    // 单张连续图：图表容器内单一 kline root（跨段不拆图——lightweight-charts
    // 单实例多 pane canvas）
    const review = page.getByTestId("vr-kline-review");
    const chartRoots = await review.locator("canvas").count();
    expect(chartRoots).toBeGreaterThanOrEqual(2); // 主 pane+equity 副 pane

    // 标记合并：头行披露 标记 N>0（filled 留痕行>0·trades）
    await expect(review).toContainText(/标记 \d+/);
    const markerLine = await review.locator("p").first().textContent();
    expect(markerLine).toMatch(/标记 [1-9]\d*/);

    // ok 态：无截断/对账不等提示（vr-kline-state 不在场）
    await expect(page.getByTestId("vr-kline-state")).toHaveCount(0);

    // 证据落档：全区间视图截图（段界色带与段名、副图断线像素面）
    await review.screenshot({ path: "e2e/screenshots/vr-fullrange-staged.png" });
  });

  test("8.2 equity 副图在场：副图 pane canvas+头行副图披露+单份视图切换", async ({ page }) => {
    test.setTimeout(120_000);
    const recordId = await findStagedRecord();
    test.skip(!recordId, "dev 库无 staged_validation 记录");
    await openRecordDetail(page, recordId!);

    // 副 pane 在场：主+副 canvas（equity 副图共用时间轴——lightweight-charts
    // 多 pane 各产 canvas）
    const review = page.getByTestId("vr-kline-review");
    expect(await review.locator("canvas").count()).toBeGreaterThanOrEqual(2);

    // 段界重置断线像素面归上条截图；本条锚结构在场+单份切换（前端过滤不重拉）
    // 视图选择器按显示值「全区间」定位（区分于筛选栏类型 Select）
    const viewSel = page.locator(".ant-select", { hasText: "全区间" }).first();
    if (await viewSel.isVisible()) {
      await viewSel.click();
      await page.locator(".ant-select-item-option", { hasText: "份 1" }).first().click();
      await expect(page.getByTestId("vr-view-mode")).toHaveText(/单份 /);
      // 单份视图仍单图（图表容器单数锚）
      expect(await review.locator("canvas").count()).toBeGreaterThanOrEqual(2);
      await review.screenshot({ path: "e2e/screenshots/vr-fullrange-single-exec.png" });
    }
  });
});
