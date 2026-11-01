/**
 * 10 设置页对齐批单测（alignment-batch D5）。
 * B1 config 消费：masked 回显（***，不明文）+保存走 PUT（*** 原样传回由
 * 后端哨兵跳过）/B2 指标参数本地 persist（zustand persist→localStorage，
 * 重进恢复）。服务层全 mock（零外网红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), put: vi.fn() },
}));

import { Settings } from "@/pages/Settings";
import { api } from "@/services/api";
import { useChartSettings } from "@/stores/chart-settings";
import { CHART_INDICATOR_DEFAULTS } from "@/lib/constants";

const MASKED_CONFIG = {
  language: "zh-CN",
  timezone: "UTC+8",
  notify_evolution: true,
  notify_signal: true,
  binance_api_key: "***",
  binance_secret_key: "***",
  binance_connected: true,
  init_cash: 250000,
  maker_fee: 0.1,
  taker_fee: 0.1,
  max_positions: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue(MASKED_CONFIG as never);
  // 指标 store 复位（persist 内存态+localStorage 双清，防用例间污染）
  useChartSettings.setState({
    emaList: CHART_INDICATOR_DEFAULTS.ema_periods.map((period) => ({
      period,
      color: "#ffffff",
      enabled: true,
    })),
    boll: { ...CHART_INDICATOR_DEFAULTS.boll },
    rsi: { ...CHART_INDICATOR_DEFAULTS.rsi },
    vol: { ...CHART_INDICATOR_DEFAULTS.vol },
  });
  window.localStorage.removeItem("chart-indicator-settings");
});

function setup() {
  return render(<Settings />);
}

/** 切到「指标参数」tab（antd Tabs；先等 config 加载出真实内容再点） */
async function openIndicatorTab() {
  await screen.findByTestId("binance-save");
  fireEvent.click(screen.getByRole("tab", { name: "指标参数" }));
  await screen.findByLabelText("BOLL 周期");
}

describe("Settings（10 设置·对齐批）", () => {
  it("B1 masked 回显：凭证键回显 *** 全掩码（非明文），状态=GET 真值", async () => {
    setup();
    const keyField = await screen.findByTestId("binance-api-key-field");
    const keyInput = keyField.querySelector("input");
    expect(keyInput?.value).toBe("***");
    const secretInput = screen.getByTestId("binance-secret-key-field").querySelector("input");
    expect(secretInput?.value).toBe("***");
    // 状态行=GET 返回的 binance_connected（真实回显非本地推断）
    expect(screen.getByTestId("binance-connected")).toHaveTextContent("已连接");
  });

  it("B1 保存走 PUT：未修改的 *** 原样传回（后端哨兵跳过不覆盖已存密钥）", async () => {
    setup();
    await screen.findByTestId("binance-save");
    fireEvent.click(screen.getByTestId("binance-save"));
    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith("/api/config", {
        json: { binance_api_key: "***", binance_secret_key: "***" },
      });
    });
  });

  it("B1 交易配置回显 GET 值（init_cash 250000）", async () => {
    setup();
    const cashInput = await screen.findByLabelText("初始资金");
    expect(cashInput).toHaveValue("250000");
  });

  it("假「测试连接」已移除（后端无凭证校验端点，不呈现假成功）", async () => {
    setup();
    await screen.findByTestId("binance-save");
    expect(screen.queryByText("测试连接")).toBeNull();
    expect(screen.queryByText("连接成功")).toBeNull();
  });

  it("B2 指标参数本地 persist：修改后重进页面恢复（localStorage）", async () => {
    const { unmount } = render(<Settings />);
    await openIndicatorTab();
    const bollPeriod = screen.getByLabelText("BOLL 周期");
    fireEvent.change(bollPeriod, { target: { value: "30" } });
    fireEvent.blur(bollPeriod);
    // persist 写入 localStorage
    await waitFor(() => {
      const saved = window.localStorage.getItem("chart-indicator-settings");
      expect(saved).toContain('"period":30');
    });
    unmount();

    // 重进页面（新组件实例）：切 tab 后值恢复
    render(<Settings />);
    await openIndicatorTab();
    expect(screen.getByLabelText("BOLL 周期")).toHaveValue("30");
  });

  it("GET 失败兜底默认配置（init_cash 100000）", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("network") as never);
    setup();
    const cashInput = await screen.findByLabelText("初始资金");
    expect(cashInput).toHaveValue("100000");
  });
});
