/**
 * 数据管理页（路由 /data）。数据集清单（质量条内嵌）+上传对话框
 * （F0 遗留死操作 disabled 化）——文件名解析镜像后端 parse_filename。
 * 消费方：App 路由（页面入口）。
 */
import { useState, useRef, useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { LoadingOutlined } from "@ant-design/icons";
import {
  Database,
  Upload,
  Search,
  Trash2,
  FileUp,
  X,
  FileSpreadsheet,
  CheckCircle2,
} from "lucide-react";
import {
  App,
  Button as AntButton,
  Form,
  Input,
  Modal,
  Progress,
  Select,
  Skeleton,
  Table,
  Tooltip,
  type TableProps,
} from "antd";
import { GlassCard } from "@/components/GlassCard";
import { PageTransition } from "@/components/PageTransition";
import { EmptyState } from "@/components/EmptyState";
import { useImportCsv, useImportCsvBatch, useDeleteDataset, useDatasets } from "@/hooks/useDatasets";
import { formatNumber } from "@/lib/utils";
import { POLARITY, STATE, TEXT } from "@/theme/tokens";
import type { Dataset } from "@/types/api";

// ---------------------------------------------------------------------------
// 文件名解析（镜像后端 parse_filename 逻辑）
// ---------------------------------------------------------------------------
const FILENAME_RE = /^([A-Z]{3,10}USDT)-(\d+[mhdw])-/i;

function parseCsvFilename(name: string): { symbol: string; interval: string } | null {
  const m = FILENAME_RE.exec(name);
  if (m) return { symbol: m[1].toUpperCase(), interval: m[2] };
  return null;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 数据质量配置（状态非纯颜色载体：文案+数值伴随色条） */
const QUALITY_CFG: Record<
  Dataset["quality_status"],
  { value: number; color: string; label: string }
> = {
  // fe-asset-data-domain 6.1：质量档色改 tokens 实色 import（原四字面量；
  // 消费方 antd Progress strokeColor 需实色）
  complete: { value: 100, color: POLARITY.up, label: "数据质量: 完整" },
  warning: { value: 90, color: STATE.warn, label: "有缺口 <5%" },
  error: { value: 60, color: POLARITY.down, label: "缺口 >5%" },
  unknown: { value: 0, color: TEXT.tertiary, label: "未知" },
};

function QualityBar({ status }: { status: Dataset["quality_status"] }) {
  const { value, color, label } = QUALITY_CFG[status] ?? QUALITY_CFG.unknown;
  return (
    <div className="flex items-center gap-2 pb-1">
      <Progress percent={value} showInfo={false} size={{ height: 6 }} strokeColor={color} className="flex-1 !m-0" />
      <span className="text-[11px] text-text-muted">{label}</span>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <Skeleton key={i} active paragraph={false} className="rounded-lg" />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 上传对话框（表单草稿以 AntD Form 为唯一持有者——ui-state spec）
// ---------------------------------------------------------------------------
function UploadDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  // 表单草稿唯一持有者=AntD Form 实例（ui-state spec：symbol/周期/导入模式）
  const [form] = Form.useForm<{ symbol: string; interval: string; mode: string }>();

  const importCsv = useImportCsv();
  const importCsvBatch = useImportCsvBatch();

  const detectedInfo = useMemo(() => {
    if (selectedFiles.length === 0) return null;
    const first = parseCsvFilename(selectedFiles[0].name);
    if (!first) return null;
    const allMatch = selectedFiles.every(
      (f) => parseCsvFilename(f.name)?.symbol === first.symbol &&
             parseCsvFilename(f.name)?.interval === first.interval
    );
    return allMatch ? first : null;
  }, [selectedFiles]);

  const autoSymbol = detectedInfo?.symbol ?? "";
  const autoInterval = detectedInfo?.interval ?? "";
  const manualSymbol = Form.useWatch("symbol", form) ?? "";
  const effectiveSymbol = autoSymbol || manualSymbol.trim().toUpperCase();
  const effectiveInterval = autoInterval || form.getFieldValue("interval") || "1h";

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const fileList = e.target.files;
      if (!fileList) return;
      setSelectedFiles((prev) => {
        const existing = new Set(prev.map((f) => f.name));
        const newFiles = Array.from(fileList).filter((f) => !existing.has(f.name));
        return [...prev, ...newFiles];
      });
      e.target.value = "";
    },
    []
  );

  const removeFile = useCallback((index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleUpload = useCallback(async () => {
    if (selectedFiles.length === 0 || !effectiveSymbol) return;

    const formData = new FormData();
    if (selectedFiles.length === 1) {
      formData.append("file", selectedFiles[0]);
    } else {
      selectedFiles.forEach((f) => formData.append("files", f));
    }
    formData.append("symbol", effectiveSymbol);
    formData.append("interval", effectiveInterval);
    formData.append("mode", form.getFieldValue("mode") || "merge");

    try {
      if (selectedFiles.length === 1) {
        await importCsv.mutateAsync(formData);
      } else {
        await importCsvBatch.mutateAsync(formData);
      }
      onOpenChange(false);
      setSelectedFiles([]);
      form.resetFields();
    } catch {
      // 错误由 mutation onError 处理
    }
  }, [selectedFiles, effectiveSymbol, effectiveInterval, form, importCsv, importCsvBatch, onOpenChange]);

  const isPending = importCsv.isPending || importCsvBatch.isPending;

  return (
    <Modal
      open={open}
      onCancel={() => !isPending && onOpenChange(false)}
      title="上传 CSV 文件"
      width={560}
      footer={[
        <AntButton key="cancel" onClick={() => onOpenChange(false)} disabled={isPending}>
          取消
        </AntButton>,
        <AntButton
          key="upload"
          type="primary"
          onClick={handleUpload}
          disabled={selectedFiles.length === 0 || !effectiveSymbol || isPending}
          icon={isPending ? <LoadingOutlined /> : null}
        >
          上传 {selectedFiles.length > 1 ? `(${selectedFiles.length} 个文件)` : ""}
        </AntButton>,
      ]}
    >
      <p className="text-xs text-text-secondary mb-4">
        支持多文件上传，自动从文件名识别 Symbol 和周期（如 BTCUSDT-30m-2026-03.csv）
      </p>

      <div className="flex flex-col gap-4 py-2">
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          multiple
          className="hidden"
          onChange={handleFileChange}
        />

        <AntButton block className="!justify-start !h-auto !py-3" onClick={() => fileInputRef.current?.click()}>
          <FileUp />
          <span className="text-text-secondary">点击选择 CSV 文件（支持多选）</span>
        </AntButton>

        {selectedFiles.length > 0 && (
          <div className="flex flex-col gap-1.5 max-h-48 overflow-y-auto">
            {selectedFiles.map((file, idx) => {
              const parsed = parseCsvFilename(file.name);
              return (
                <div
                  key={`${file.name}-${idx}`}
                  className="flex items-center gap-2 rounded-md bg-bg-elevated px-3 py-2 text-xs"
                >
                  <FileSpreadsheet className="text-accent-gold" />
                  <span className="flex-1 truncate text-text-primary">{file.name}</span>
                  <span className="text-text-muted shrink-0">{formatFileSize(file.size)}</span>
                  {parsed && (
                    <span className="shrink-0 text-profit text-[10px]">
                      <CheckCircle2 className="mr-0.5 align-middle" />
                      {parsed.symbol} {parsed.interval}
                    </span>
                  )}
                  <AntButton
                    type="text"
                    size="small"
                    aria-label={`移除文件 ${file.name}`}
                    onClick={() => removeFile(idx)}
                  >
                    <X />
                  </AntButton>
                </div>
              );
            })}
            <span className="text-[11px] text-text-muted">
              共 {selectedFiles.length} 个文件，{formatFileSize(selectedFiles.reduce((s, f) => s + f.size, 0))}
            </span>
          </div>
        )}

        {detectedInfo && (
          <div className="flex items-center gap-2 rounded-md bg-profit/10 px-3 py-2 text-xs text-profit">
            <CheckCircle2 />
            <span>已自动识别: {detectedInfo.symbol} / {detectedInfo.interval}</span>
          </div>
        )}

        <Form form={form} layout="vertical" initialValues={{ interval: "1h", mode: "merge" }} className="!mb-0">
          <div className="grid grid-cols-2 gap-3">
            <Form.Item
              label="Symbol"
              name="symbol"
              rules={[{ required: !autoSymbol, message: "请输入交易对（如 BTCUSDT）" }]}
              className="!mb-2"
            >
              <Input placeholder="BTCUSDT" disabled={!!autoSymbol} value={autoSymbol || undefined} />
            </Form.Item>
            <Form.Item label="周期" name="interval" className="!mb-2">
              <Select
                disabled={!!autoInterval}
                value={autoInterval || undefined}
                options={["15m", "30m", "1h", "4h", "1d", "3d", "1w"].map((v) => ({ value: v, label: v }))}
              />
            </Form.Item>
          </div>
          <Form.Item label="导入模式" name="mode" className="!mb-0">
            <Select
              options={[
                { value: "merge", label: "合并" },
                { value: "replace", label: "替换" },
                { value: "new", label: "新建" },
              ]}
            />
          </Form.Item>
        </Form>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
import { DualIntegrityMatrix, FreshnessTableFixed } from "@/components/data/IntegrityPanels";

export function DataManagement() {
  const { modal } = App.useApp();
  const [search, setSearch] = useState("");
  const normalizedSearch = search.trim().toLowerCase();
  // 09 页完整性两区：fe-asset-data-domain 4.3 双币并列（R9 生效态）——
  // matrix symbol 双态输入框退役（原 CARD-02 F2 提交态机制随单币消费消亡）
  const [monthsBack, setMonthsBack] = useState(6);

  const [uploadOpen, setUploadOpen] = useState(false);
  const deleteDataset = useDeleteDataset();

  // 数据查询下沉既有 hook（G5 修正：原页面内联 queryFn 与 useDatasets 重复）
  const { data, isLoading, isError, refetch } = useQuery(useDatasets());

  const allDatasets: Dataset[] = data?.items ?? [];

  const datasets = normalizedSearch
    ? allDatasets.filter(
        (d) =>
          d.symbol.toLowerCase().includes(normalizedSearch) ||
          d.interval.toLowerCase().includes(normalizedSearch)
      )
    : allDatasets;

  const handleDelete = useCallback(
    (ds: Dataset) => {
      // 确认交互单源=AntD modal 体系（ui-uniformity spec；删除结果经 mutation toast 反馈）
      modal.confirm({
        title: "删除数据集",
        content: `确定要删除 ${ds.symbol} (${ds.interval}) 数据集吗？此操作不可撤销。`,
        okText: "删除",
        okButtonProps: { danger: true, loading: deleteDataset.isPending },
        cancelText: "取消",
        onOk: async () => {
          await deleteDataset.mutateAsync(ds.dataset_id);
        },
      });
    },
    [modal, deleteDataset]
  );

  const columns: TableProps<Dataset>["columns"] = [
    {
      title: "币种",
      dataIndex: "symbol",
      render: (v: string) => <span className="font-medium text-text-primary">{v}</span>,
    },
    { title: "名称", dataIndex: "symbol", className: "text-text-secondary" },
    {
      title: "周期",
      dataIndex: "interval",
      render: (v: string) => (
        <span className="inline-flex items-center rounded-md bg-state-warn/10 px-1.5 py-0.5 text-xs text-text-secondary">
          {v}
        </span>
      ),
    },
    {
      title: "日期范围",
      key: "date_range",
      render: (_, ds) =>
        ds.time_start && ds.time_end
          ? `${ds.time_start.slice(0, 10)} ~ ${ds.time_end.slice(0, 10)}`
          : "-",
      className: "text-text-secondary text-xs font-num",
    },
    {
      title: "K线数量",
      dataIndex: "row_count",
      align: "right",
      render: (v: number) => formatNumber(v, 0),
      className: "font-num text-text-secondary",
    },
    {
      title: "操作",
      key: "actions",
      align: "right",
      render: (_, ds) => (
        <div className="flex items-center justify-end gap-1">
          {/* fe-asset-data-domain 4.2：预览/在实验室打开两死操作移除
              （D4 裁决——原型 09 页无此操作，「接真」无原型依据不取） */}
          <Tooltip title="删除">
            <AntButton
              type="text"
              size="small"
              danger
              icon={<Trash2 />}
              aria-label={`删除数据集 ${ds.symbol} ${ds.interval}`}
              onClick={() => handleDelete(ds)}
            />
          </Tooltip>
        </div>
      ),
    },
  ];

  // 空态（非失败）：引导导入
  if (!isLoading && allDatasets.length === 0) {
    return (
      <PageTransition>
        <EmptyState
          icon={Database}
          title="还没有导入任何数据集"
          description="上传 CSV 文件导入历史数据，开始量化分析"
          actions={[
            {
              label: "导入 CSV 文件",
              onClick: () => setUploadOpen(true),
            },
          ]}
        />
        <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} />
      </PageTransition>
    );
  }

  return (
    <PageTransition>
      {/* 完整性月矩阵+保鲜表两区（09-B2r/09-B6r） */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <GlassCard className="p-3" hover={false}>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs text-text-secondary">完整性月矩阵（双币并列）</span>
            <select
              value={monthsBack}
              onChange={(e) => setMonthsBack(Number(e.target.value))}
              className="rounded border border-border-default bg-transparent px-1 py-0.5 text-xs text-text-secondary"
              aria-label="月窗"
            >
              <option value={6}>近 6 月</option>
              <option value={12}>近 12 月</option>
            </select>
          </div>
          {/* fe-asset-data-domain 4.3：双币两请求并列（R9 生效态）——
              单 symbol 输入框退役 */}
          <DualIntegrityMatrix monthsBack={monthsBack} />
        </GlassCard>
        <GlassCard className="p-3" hover={false}>
          <span className="mb-2 block text-xs text-text-secondary">数据保鲜（guardian 十二路满格）</span>
          {/* fe-asset-data-domain 4.3：12 键六态+未扫占位（键集不全时恒满格） */}
          <FreshnessTableFixed />
        </GlassCard>
      </div>
      <div className="flex flex-col gap-4">
        {/* 工具栏 */}
        <GlassCard className="p-4" hover={false}>
          <div className="flex flex-wrap items-center gap-3">
            <AntButton
              size="small"
              type="primary"
              icon={<Upload />}
              onClick={() => setUploadOpen(true)}
            >
              上传 CSV 文件
            </AntButton>
            {/* fe-asset-data-domain 4.2：Binance 导入死按钮移除（D4——同上） */}
            <div className="relative ml-auto">
              <Input
                placeholder="搜索币种..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                prefix={<Search className="text-text-muted" />}
                className="w-56 text-xs"
                allowClear
              />
            </div>
          </div>
        </GlassCard>

        {/* 数据集表（每行下方质量条：expandable 无缝嵌入） */}
        <GlassCard className="p-0 overflow-hidden" hover={false}>
          {isError ? (
            /* fe-asset-data-domain 4.1：故障态可见（W0）——数据源失败呈现
               错误行+重试，禁静默空表（TanStack retry 兜底自动重拉） */
            <div className="flex flex-col items-center gap-2 py-10">
              <p className="text-sm text-loss">数据集源暂不可达</p>
              <AntButton size="small" onClick={() => refetch()}>
                重试
              </AntButton>
            </div>
          ) : isLoading ? (
            <div className="p-4">
              <TableSkeleton />
            </div>
          ) : (
            <Table<Dataset>
              rowKey="dataset_id"
              columns={columns}
              dataSource={datasets}
              size="middle"
              pagination={false}
              showHeader
              expandable={{
                showExpandColumn: false,
                defaultExpandAllRows: true,
                expandedRowRender: (ds) => <QualityBar status={ds.quality_status} />,
              }}
              locale={{ emptyText: "没有匹配的数据集" }}
            />
          )}
        </GlassCard>

        {/* 页脚提示 */}
        <p className="text-xs text-text-muted text-center">
          建议至少准备6个月历史数据用于回测，支持 Binance Data Vision CSV
        </p>

        <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} />
      </div>
    </PageTransition>
  );
}
