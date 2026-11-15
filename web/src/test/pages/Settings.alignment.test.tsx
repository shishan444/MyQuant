/**
 * 10 设置页对齐批单测（alignment-batch D5；fix-audit-disclosure-security
 * S-01 拆链改写：凭据表单退役，卡片承载配置指引——币安凭据经
 * data/credentials.json 本地文件配置）/B2 指标参数本地 persist
 * （zustand persist→localStorage，重进恢复）。服务层全 mock（零外网红线）。
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

const CONFIG_NO_CREDENTIALS = {
  language: "zh-CN",
  timezone: "UTC+8",
  notify_evolution: true,
  notify_signal: true,
  init_cash: 250000,
  maker_fee: 0.1,
  taker_fee: 0.1,
  max_positions: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue(CONFIG_NO_CREDENTIALS as never);
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
  await screen.findByText("Binance API 配置");
  fireEvent.click(screen.getByRole("tab", { name: "指标参数" }));
  await screen.findByLabelText("BOLL 周期");
}

describe("Settings（10 设置·对齐批）", () => {
  it("S-01 拆链：凭据卡承载配置指引（data/credentials.json），无表单无明文", async () => {
    setup();
    const hint = await screen.findByTestId("binance-credentials-hint");
    expect(hint).toHaveTextContent("data/credentials.json");
    // 凭据输入表单退役（键不再经页面流转）
    expect(screen.queryByTestId("binance-api-key-field")).toBeNull();
    expect(screen.queryByTestId("binance-secret-key-field")).toBeNull();
    expect(screen.queryByTestId("binance-connected")).toBeNull();
  });

  it("S-01 拆链：凭据卡无保存动作（PUT 零凭据面）", async () => {
    setup();
    await screen.findByTestId("binance-credentials-hint");
    expect(screen.queryByTestId("binance-save")).toBeNull();
    expect(api.put).not.toHaveBeenCalled();
  });

  it("B1 交易配置回显 GET 值（init_cash 250000）", async () => {
    setup();
    const cashInput = await screen.findByLabelText("初始资金");
    expect(cashInput).toHaveValue("250000");
  });

  it("假「测试连接」已移除（后端无凭证校验端点，不呈现假成功）", async () => {
    setup();
    await screen.findByTestId("binance-credentials-hint");
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

  it("GET 失败可见（fe-asset-data-domain 5.1）：错误态+重试，静默兜底退役", async () => {
    // 行为变更锚：原「失败→DEFAULT_CONFIG 兜底渲染表单」被 W0 裁决废除
    // （失败伪装成功）——现失败呈现错误态+重试钮，表单不渲染
    vi.mocked(api.get).mockRejectedValue(new Error("network") as never);
    setup();
    expect(await screen.findByText("配置源暂不可达")).toBeInTheDocument();
    // antd 两字按钮自动加空格（「重 试」）——匹配器容空白
    expect(screen.getByText(/重\s*试/)).toBeInTheDocument();
    expect(screen.queryByLabelText("初始资金")).not.toBeInTheDocument();
    // 重试点击 → 配置源重发（t3r5 G6——loadConfig 再调 GET：mount 1 次+重试 1 次）
    fireEvent.click(screen.getByText(/重\s*试/));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });
});
