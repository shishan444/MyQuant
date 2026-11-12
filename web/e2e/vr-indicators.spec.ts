/**
 * 验证记录 DNA 指标叠加 e2e（upgrade-vr-indicator-overlay REQ-9 第三条）：
 * 打开含 strategy_id 且 DNA 引用 BB 的记录（dev 库实锚 rec_25228eab97f9 →
 * st_8fac17534a47，BB {period:20,std:2} field=middle——设计卡实测锚），
 * 断言 DNA 指标徽标默认可见+参数与策略一致+周期切换工具区在场。
 * 指标线像素面归 canvas（jsdom/e2e 不做像素断言——参数一致经徽标串锚）。
 */
import { test, expect } from "@playwright/test";

const API = "http://localhost:8000";

async function findRecordWithDnaOverlay(): Promise<{ recordId: string; badge: string } | null> {
  const list = await fetch(`${API}/api/research/validation-records?limit=30`);
  if (!list.ok) return null;
  const { records } = (await list.json()) as { records: Array<{ record_id: string }> };
  for (const r of records.slice(0, 12)) {
    const d = await fetch(`${API}/api/research/validation-records/${r.record_id}/detail`);
    if (!d.ok) continue;
    const detail = (await d.json()) as { strategy_id?: string };
    if (!detail.strategy_id) continue;
    const s = await fetch(`${API}/api/strategies/${detail.strategy_id}`);
    if (!s.ok) continue;
    const dna = ((await s.json()) as { dna?: { signal_genes?: Array<{ indicator?: string; params?: Record<string, number>; field?: string }> } }).dna;
    const bb = (dna?.signal_genes ?? []).find((g) => g.indicator === "BB");
    if (bb?.params) {
      return {
        recordId: r.record_id,
        badge: `BB(${bb.params.period},${bb.params.std})`,
      };
    }
  }
  return null;
}

test("REQ-9 第三条：DNA 引用指标默认可见+参数与策略一致+周期工具区", async ({ page }) => {
  test.setTimeout(120_000);
  const target = await findRecordWithDnaOverlay();
  test.skip(!target, "dev 库无 DNA 引用 BB 的验证记录——造数归后续 seed 形制");
  const { recordId, badge } = target!;

  await page.goto("/verify");
  const card = page.getByTestId(`vr-card-${recordId}`);
  await card.first().waitFor({ timeout: 15_000 });
  await card.first().scrollIntoViewIfNeeded();
  await card.first().click();

  // DNA 徽标默认可见+参数与策略一致（徽标串即参数披露——无手动开关介入）
  const badges = page.getByTestId("vr-dna-badges");
  await expect(badges).toBeVisible({ timeout: 20_000 });
  await expect(badges).toContainText(badge);
  // 手动开关不开启时指标仍默认叠加（契约 I——DNA 叠加优先于手动开关）
  await expect(page.getByTestId("vr-manual-ema")).not.toBeChecked();

  // 周期切换工具区在场（八档 Select+基准徽标）
  await expect(page.getByTestId("vr-base-tf")).toBeVisible();
  await expect(page.getByTestId("vr-tf-select")).toBeVisible();

  // 证据落档
  await page.getByTestId("vr-kline-review").screenshot({ path: "e2e/screenshots/vr-indicators-dna.png" });
});
