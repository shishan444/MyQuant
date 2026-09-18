import { defineConfig } from "@playwright/test";

/**
 * L3 主链路 e2e（G17.2）：前后端真实服务。
 * 后端由 webServer 第二项拉起（若已在跑则复用 reuseExistingServer）。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: "http://localhost:5173",
    screenshot: "only-on-failure",
  },
  expect: {
    toHaveScreenshot: { maxDiffPixels: 100 },
  },
  webServer: [
    {
      command: "../../MyQuant/venv/bin/python ../../MyQuant/scripts/api_run.py",
      url: "http://localhost:8000/api/health",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: "npx vite --port 5173",
      url: "http://localhost:5173",
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
  outputDir: "./e2e/.results",
});
