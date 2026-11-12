/**
 * Verify 页测试（G7 AntD 迁移，新建）。
 * 断言语义=用户可见行为：策略库空态引导/配置区（预设+日期区间+参数）/历史视图切换。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { Verify } from "@/pages/Verify";
import { antdThemeConfig } from "@/theme/antdTheme";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { Strategy } from "@/types/api";

vi.mock("@/hooks/useStrategies", () => ({
  useStrategies: vi.fn(),
  // upgrade-vr-indicator-overlay：RecordDetail 引入 useStrategy（queryOptions
  // 形制）——未选中记录时 enabled=false 不发请求，桩返回空 queryOptions
  useStrategy: vi.fn(() => ({ queryKey: ["strategies", "detail", ""], queryFn: vi.fn(), enabled: false })),
  useVerifyStream: vi.fn(),
  useVerifySessions: vi.fn(),
  useSessionResults: vi.fn(),
}));

// Q2 修复（fix/fe-validation-forms-remediation）：浏览骨架数据源 mock——
// 原缺失时 RecordsTable 以错误态渲染（「验证记录源暂不可达」）因无断言侥幸绿
// upgrade-validation-records-list 5.1：卡片流消费面扩展（union-balance+
// 类型三枚+排序枚举——RecordsBrowser 组合）；响应补计数两键
vi.mock("@/services/validationRecords", () => ({
  getValidationRecordsPage: vi.fn().mockResolvedValue({
    records: [], total: 0, offset: 0, limit: 50, has_more: false,
    verdict_counts: { pass: 0, fail: 0, inconclusive: 0 },
    unsortable_count: 0,
  }),
  getValidationRecordDetail: vi.fn(),
  getUnionBalance: vi.fn(),
  VALIDATION_LEVELS: [
    "isolated_backtest", "random_window", "staged_validation", "composite", "component",
  ],
  VALIDATION_VERDICTS: ["pass", "fail", "inconclusive"],
  VALIDATION_SORTS: ["time_desc", "total_return_desc", "verdict_quality"],
  VALIDATION_TYPE_OPTIONS: [
    { value: "isolated_backtest", label: "隔离验证" },
    { value: "random_window", label: "随机窗口" },
    { value: "staged_validation", label: "分段验证" },
  ],
}));

import { useStrategies, useVerifyStream, useVerifySessions, useSessionResults } from "@/hooks/useStrategies";

function makeStrategy(): Strategy {
  return {
    strategy_id: "s1",
    name: "Test Strategy",
    symbol: "BTCUSDT",
    timeframe: "4h",
    source: "lab",
    generation: 0,
    verify_count: 0,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    dna: {
      signal_genes: [],
      logic_genes: { entry_logic: "AND", exit_logic: "AND" },
      execution_genes: { timeframe: "4h", symbol: "BTCUSDT" },
      risk_genes: { stop_loss: 0.05, take_profit: 0.1, position_size: 0.3, leverage: 1, direction: "long" },
      generation: 0,
      parent_ids: [],
      mutation_ops: [],
    },
  };
}

function setup(items: Strategy[]) {
  vi.mocked(useStrategies).mockReturnValue({
    queryKey: ["strategies"],
    queryFn: () => Promise.resolve({ items, total: items.length }),
    isLoading: false,
  } as never);
  vi.mocked(useVerifyStream).mockReturnValue({
    status: "idle",
    progress: { current: 0, total: 0 },
    currentGroup: "",
    rangeLabel: "",
    results: [],
    summary: [],
    sessionId: null,
    error: null,
    start: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
  } as never);
  vi.mocked(useVerifySessions).mockReturnValue({
    queryKey: ["verify-sessions"],
    queryFn: () => Promise.resolve({ items: [], total: 0 }),
  } as never);
  vi.mocked(useSessionResults).mockReturnValue({ queryKey: ["x"], queryFn: vi.fn() } as never);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
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
  return { user };
}

describe("Verify（AntD）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("策略库空态：呈现引导与两个跳转入口", async () => {
    setup([]);
    expect(await screen.findByText("策略库为空")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "前往策略库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "前往实验室" })).toBeInTheDocument();
  });

  it("配置区：策略计数+日期预设+日期输入", async () => {
    const { user } = setup([makeStrategy()]);
    // fe-validation-records-page：发起器默认折叠——先开折叠区
    await user.click(screen.getByRole("button", { name: /发起器 · 历史会话/ }));
    // 计数经主按钮锚定（文本跨 JSX 节点）：开始验证 (1)
    expect(await screen.findByRole("button", { name: /开始验证\s*\(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "近3月" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /自定义区间/ })).toBeInTheDocument();
    // 日期输入（原生 date input）
    expect(document.querySelectorAll('input[type="date"]').length).toBeGreaterThanOrEqual(2);
  });

  it("历史切换：点击进入历史视图（空记录态）再返回", async () => {
    const { user } = setup([makeStrategy()]);
    // fe-validation-records-page：先开折叠区（发起器默认折叠）
    await user.click(screen.getByRole("button", { name: /发起器 · 历史会话/ }));
    await screen.findByRole("button", { name: /开始验证\s*\(1\)/ });
    await user.click(screen.getByRole("button", { name: /历史会话/ }));
    expect(await screen.findByText("暂无验证记录")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /返回发起器/ }));
    expect(await screen.findByRole("button", { name: /开始验证\s*\(1\)/ })).toBeInTheDocument();
  });
});
