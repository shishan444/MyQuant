/**
 * DataManagement 页测试（G5 AntD 迁移重写）。
 * 断言语义=用户可见行为：空态引导/加载占位/数据行渲染/搜索过滤/删除确认/上传弹层表单。
 * 交互一律 userEvent。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntdApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { DataManagement } from "@/pages/DataManagement";
import { antdThemeConfig } from "@/theme/antdTheme";
import type { Dataset } from "@/types/api";

vi.mock("@/hooks/useDatasets", () => ({
  useDatasets: vi.fn(),
  useImportCsv: vi.fn(),
  useImportCsvBatch: vi.fn(),
  useDeleteDataset: vi.fn(),
}));

import { useDatasets, useDeleteDataset, useImportCsv, useImportCsvBatch } from "@/hooks/useDatasets";

function makeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    dataset_id: "d1",
    symbol: "BTCUSDT",
    interval: "1h",
    parquet_path: "/tmp/d1.parquet",
    row_count: 1000,
    time_start: "2025-01-01T00:00:00Z",
    time_end: "2025-06-01T00:00:00Z",
    file_size_bytes: 1024,
    source: "csv_import",
    quality_status: "complete",
    gap_count: 0,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    ...overrides,
  };
}

function setup(reactQueryData?: { items?: Dataset[]; isLoading?: boolean }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // mock queryOptions：queryFn 返回注入数据（加载态=永 pending）
  const items = reactQueryData?.items;
  vi.mocked(useDatasets).mockReturnValue({
    queryKey: ["datasets"],
    queryFn:
      reactQueryData?.isLoading
        ? () => new Promise(() => {})
        : () => Promise.resolve({ items: items ?? [], total: items?.length ?? 0 }),
  } as never);
  const mutateAsync = vi.fn();
  vi.mocked(useDeleteDataset).mockReturnValue({
    mutateAsync,
    isPending: false,
  } as never);
  const noopMutation = { mutateAsync: vi.fn(), isPending: false };
  vi.mocked(useImportCsv).mockReturnValue(noopMutation as never);
  vi.mocked(useImportCsvBatch).mockReturnValue(noopMutation as never);
  const user = userEvent.setup();
  const utils = render(
    <QueryClientProvider client={qc}>
      <ConfigProvider theme={antdThemeConfig} locale={zhCN}>
        <AntdApp>
          <DataManagement />
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>,
  );
  return { user, qc, utils, mutateAsync };
}

describe("DataManagement（AntD）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("空态：无数据集时呈现导入引导", async () => {
    setup({ items: [] });
    expect(await screen.findByText("还没有导入任何数据集")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导入 CSV 文件" })).toBeInTheDocument();
  });

  it("加载态：呈现骨架占位", () => {
    setup({ isLoading: true });
    expect(document.querySelector(".ant-skeleton")).toBeInTheDocument();
  });

  it("成功态：表格渲染数据集行与质量条文案", async () => {
    setup({ items: [makeDataset()] });
    expect(await screen.findAllByText("BTCUSDT")).not.toHaveLength(0); // 币种+名称两列
    expect(screen.getByText("1h")).toBeInTheDocument();
    expect(screen.getByText("1000")).toBeInTheDocument(); // formatNumber(1000, 0)=toFixed(0)
    expect(screen.getByText("数据质量: 完整")).toBeInTheDocument();
    expect(screen.getByText("2025-01-01 ~ 2025-06-01")).toBeInTheDocument();
  });

  it("搜索过滤：输入 ETH 后 BTC 行消失", async () => {
    setup({
      items: [makeDataset(), makeDataset({ dataset_id: "d2", symbol: "ETHUSDT", interval: "4h" })],
    });
    await screen.findAllByText("BTCUSDT");
    const search = screen.getByPlaceholderText("搜索币种...");
    await userEvent.type(search, "ETH");
    expect(screen.queryAllByText("BTCUSDT")).toHaveLength(0);
    expect(await screen.findAllByText("ETHUSDT")).not.toHaveLength(0);
  });

  it("删除：确认弹层出现且确认后调用删除", async () => {
    const { user, mutateAsync } = setup({ items: [makeDataset()] });
    await screen.findAllByText("BTCUSDT");
    await user.click(screen.getByRole("button", { name: "删除数据集 BTCUSDT 1h" }));
    // AntD modal.confirm 渲染到 body
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/此操作不可撤销/)).toBeInTheDocument();
    // footer 按钮渲染在 confirm 容器（dialog 同级），用全屏查询
    const okBtn = screen.getAllByRole("button", { name: /^删\s*除$/ })[0]; // 精确名（排除行内删除图标按钮的 aria-label）
    await user.click(okBtn);
    await vi.waitFor(() => expect(mutateAsync).toHaveBeenCalledWith("d1"));
  });

  it("死操作 disabled：预览/Binance 导入不可点（可识别的不可用态）", async () => {
    setup({ items: [makeDataset()] });
    await screen.findAllByText("BTCUSDT");
    expect(screen.getByRole("button", { name: "预览数据集" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "在实验室打开" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "从 Binance 导入" })).toBeDisabled();
  });

  it("上传弹层：打开后表单三控件（Symbol 必填提示经 Form rules）", async () => {
    const { user } = setup({ items: [makeDataset()] });
    await screen.findAllByText("BTCUSDT");
    // 工具栏上传按钮（空态外的按钮群中，取第一个可点的「上传 CSV 文件」）
    await user.click(screen.getAllByRole("button", { name: /上传 CSV 文件/ })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("上传 CSV 文件")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Symbol")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("导入模式")).toBeInTheDocument();
    // 未选文件时上传按钮禁用
    expect(within(dialog).getByRole("button", { name: /^上传/ })).toBeDisabled();
  });
});
