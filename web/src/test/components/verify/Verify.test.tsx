/**
 * Verify 页测试（记录浏览壳形制——fix-replay-gap-alignment U8）。
 * 发起器与历史会话视图随 verify 会话体系退役删除；本页缩为
 * 头部+RecordsBrowser 浏览骨架。断言语义=用户可见行为：
 * 标题在页/浏览骨架渲染/退役区块零残留。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { Verify } from "@/pages/Verify";
import { antdThemeConfig } from "@/theme/antdTheme";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

// RecordDetail 引入 useStrategy（queryOptions 形制）——未选中记录时
// enabled=false 不发请求，桩返回空 queryOptions
vi.mock("@/hooks/useStrategies", () => ({
  useStrategies: vi.fn(),
  useStrategy: vi.fn(() => ({ queryKey: ["strategies", "detail", ""], queryFn: vi.fn(), enabled: false })),
}));

// 浏览骨架数据源 mock（响应计数五键对齐端点契约——
// fix-replay-list-channel；TYPE_OPTIONS 对齐写入白名单两值）
vi.mock("@/services/validationRecords", () => ({
  getValidationRecordsPage: vi.fn().mockResolvedValue({
    records: [], total: 0, offset: 0, limit: 50, has_more: false,
    verdict_counts: { pass: 0, fail: 0, inconclusive: 0, sandbox: 0, replay: 0 },
    unsortable_count: 0,
  }),
  getValidationRecordDetail: vi.fn(),
  getUnionBalance: vi.fn(),
  VALIDATION_TYPE_OPTIONS: [
    { value: "sandbox_backtest", label: "沙盒回测" },
    { value: "replay", label: "回放" },
  ],
}));

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ConfigProvider theme={antdThemeConfig} locale={zhCN}>
          <AntdApp>
            <Verify />
          </AntdApp>
        </ConfigProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Verify（记录浏览壳）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("头部标题+浏览骨架在页（RecordsBrowser 组合渲染）", async () => {
    setup();
    expect(screen.getByRole("heading", { name: "验证记录" })).toBeInTheDocument();
    // 浏览骨架主体：筛选栏（vr-filter-bar）真实渲染
    expect(await screen.findByTestId("vr-filter-bar")).toBeInTheDocument();
  });

  it("verify 会话区块零残留：发起器与历史会话入口不在页（U8 退役锚）", async () => {
    setup();
    await screen.findByTestId("vr-filter-bar");
    expect(screen.queryByText(/发起器 · 历史会话/)).not.toBeInTheDocument();
    expect(screen.queryByText("历史会话")).not.toBeInTheDocument();
    expect(screen.queryByText(/开始验证/)).not.toBeInTheDocument();
    expect(screen.queryByText("策略库为空")).not.toBeInTheDocument();
  });
});
