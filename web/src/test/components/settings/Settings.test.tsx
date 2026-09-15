/**
 * Settings 页测试（G6 AntD 迁移重写，新建）。
 * 断言语义=用户可见行为：tab 切换/通用保存链路/Binance 测试连接/模拟交易数值校验
 * （失败保留草稿+错误就近——ui-state spec 场景）/指标 tab EMA 增删。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Settings } from "@/pages/Settings";
import { api } from "@/services/api";
import { antdThemeConfig } from "@/theme/antdTheme";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

vi.mock("@/services/api", () => ({
  api: { get: vi.fn(), put: vi.fn() },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const baseConfig = {
  language: "zh-CN",
  timezone: "UTC+8",
  notify_evolution: true,
  notify_signal: true,
  binance_api_key: "",
  binance_secret_key: "",
  binance_connected: false,
  init_cash: 100000,
  maker_fee: 0.1,
  taker_fee: 0.1,
  max_positions: 1,
};

function setup(config = baseConfig) {
  vi.mocked(api.get).mockResolvedValue({ data: config });
  vi.mocked(api.put).mockResolvedValue({ data: config });
  const user = userEvent.setup();
  render(
    <ConfigProvider theme={antdThemeConfig} locale={zhCN}>
      <AntdApp>
        <Settings />
      </AntdApp>
    </ConfigProvider>,
  );
  return { user };
}

describe("Settings（AntD）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("加载配置后呈现四 tab 与通用区块", async () => {
    setup();
    expect(await screen.findByText("通用设置")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "指标参数" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "数据管理" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "关于" })).toBeInTheDocument();
    expect(screen.getByText("Binance API 配置")).toBeInTheDocument();
    expect(screen.getByText("模拟交易配置")).toBeInTheDocument();
  });

  it("通用保存：点击保存更改触发 PUT /api/config", async () => {
    const { user } = setup();
    await screen.findByText("通用设置");
    await user.click(screen.getByRole("button", { name: /保\s*存更改/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect(vi.mocked(api.put).mock.calls[0][0]).toBe("/api/config");
  });

  it("模拟交易校验：输入负初始资金保存→错误就近展示且不发 PUT", async () => {
    const { user } = setup();
    await screen.findByText("模拟交易配置");
    const cashInput = screen.getByRole("spinbutton", { name: /初始资金/ });
    await user.clear(cashInput);
    await user.type(cashInput, "-5");
    // 两个「保存」按钮（Binance 区块+模拟交易区块），取后者
    const saveBtns = screen.getAllByRole("button", { name: /^保\s*存$/ });
    await user.click(saveBtns[saveBtns.length - 1]);
    await waitFor(() =>
      expect(screen.getByText("初始资金须为正数")).toBeInTheDocument(),
    );
    expect(api.put).not.toHaveBeenCalled();
    // 失败保留草稿（ui-state spec）
    expect(cashInput).toHaveValue("-5"); // InputNumber 草稿以字符串保留
  });

  it("指标 tab：添加 EMA 出现新行并可删除", async () => {
    const { user } = setup();
    await screen.findByText("通用设置");
    await user.click(screen.getByRole("tab", { name: "指标参数" }));
    expect(await screen.findByText("EMA 配置")).toBeInTheDocument();
    // 持久化 store 里可能有默认 EMA；添加后行数增加
    const before = screen.getAllByLabelText(/EMA\d+ 启用/).length;
    await user.click(screen.getByRole("button", { name: /添加 EMA/ }));
    expect(screen.getAllByLabelText(/EMA\d+ 启用/)).toHaveLength(before + 1);
    // 删除刚添加的（最后一个）
    const delButtons = screen.getAllByRole("button", { name: /删除 EMA/ });
    await user.click(delButtons[delButtons.length - 1]);
    await waitFor(() =>
      expect(screen.getAllByLabelText(/EMA\d+ 启用/)).toHaveLength(before),
    );
  });

  it("Binance 测试连接：空凭证提示错误", async () => {
    const { user } = setup();
    await screen.findByText("Binance API 配置");
    const { toast } = await import("sonner");
    await user.click(screen.getByRole("button", { name: /测试连接/ }));
    // 测试连接内置 1.5s 假延迟
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("请填写 API Key 和 Secret Key"), { timeout: 4000 });
  });
});
