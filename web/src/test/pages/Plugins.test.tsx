/**
 * 11 插件与组件页单测（alignment-batch D6）。
 * B1 公式插件表（warmup null 语义）/B2 模型插件三态/B3 词表卡（族分组+
 * caliber_note 原文）/B4 四层池静态名单+registry 动态数/B5 协议要点；
 * 三态纪律（loading/error/empty）。服务层全 mock（零外网红线）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/services/plugins", () => ({
  getFormulaPlugins: vi.fn(),
  getModelPlugins: vi.fn(),
  getVocabulary: vi.fn(),
  getSignalColumns: vi.fn(),
}));

import { Plugins } from "@/pages/Plugins";
import * as svc from "@/services/plugins";

const FORMULA = {
  items: [
    {
      plugin_id: "fp1", name: "fx_q75_vol", version: 2,
      expression: "rolling_quantile(close, 100, 0.75)",
      output_columns: ["fx_q75_vol"], status: "valid", reject_reason: null,
      strategy_visible: 1, created_at: "2026-10-01T00:00:00Z",
      warmup_bars: 101, dependency_closure: ["fx_q75_vol"],
    },
    {
      plugin_id: "fp2", name: "fx_bad", version: 1,
      expression: "unknown_word(close)",
      output_columns: ["fx_bad"], status: "rejected",
      reject_reason: "词表白名单校验未通过：unknown_word",
      strategy_visible: 0, created_at: "2026-10-02T00:00:00Z",
      warmup_bars: null, dependency_closure: [],
    },
  ],
  total: 2,
};

const MODEL = {
  items: [
    { plugin_id: "mp1", name: "rolling_logistic_a", version: 1, config: "{}", signal_column: "ml_a", status: "pending", reject_reason: null, oos_summary: null, strategy_visible: 1, created_at: "2026-10-03T00:00:00Z" },
    { plugin_id: "mp2", name: "rolling_logistic_b", version: 1, config: "{}", signal_column: "ml_b", status: "valid", reject_reason: null, oos_summary: { auc: 0.61 }, strategy_visible: 1, created_at: "2026-10-04T00:00:00Z" },
    { plugin_id: "mp3", name: "rolling_logistic_c", version: 1, config: "{}", signal_column: "ml_c", status: "rejected", reject_reason: "训练超时", strategy_visible: 0, created_at: "2026-10-05T00:00:00Z" },
  ],
  total: 3,
};

const VOCAB = {
  total: 26,
  families: {
    "时序变换": ["abs", "corr", "diff"],
    "时序结构": ["crossover", "crossunder"],
    "逐元素": ["eq", "gt"],
    "数学词汇": ["kalman", "kalman_slope"],
  },
  counts: { "时序变换": 12, "时序结构": 2, "逐元素": 10, "数学词汇": 2 },
  caliber_note: "源码主口径（v2.1 修订权威口径）四族：时序变换 12 / 时序结构 2 / 逐元素 10 / 数学词汇 2。",
};

const SIGNALS = [
  { column_key: "ema", output_fields: ["ema"], source: "builtin", category: "trend", lookback: 20, strategy_visible: true, version: null, scopes: [], reference_form: {} },
  { column_key: "fx_q75_vol", output_fields: ["fx_q75_vol"], source: "formula", category: "plugin", lookback: null, strategy_visible: true, version: 2, scopes: [], reference_form: {} },
  { column_key: "ml_a", output_fields: ["ml_a"], source: "model", category: "plugin", lookback: null, strategy_visible: true, version: 1, scopes: [], reference_form: {} },
];

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Plugins />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(svc.getFormulaPlugins).mockResolvedValue(FORMULA as never);
  vi.mocked(svc.getModelPlugins).mockResolvedValue(MODEL as never);
  vi.mocked(svc.getVocabulary).mockResolvedValue(VOCAB as never);
  vi.mocked(svc.getSignalColumns).mockResolvedValue(SIGNALS as never);
});

describe("Plugins（11 插件与组件）", () => {
  it("B1 公式插件表：行字段+warmup null 呈现「—（未在册）」+rejected 附原因", async () => {
    setup();
    expect(await screen.findByTestId("formula-table")).toBeInTheDocument();
    // name 与输出列同文本（多命中合法）——至少呈现于名称位
    expect(screen.getAllByText("fx_q75_vol").length).toBeGreaterThan(0);
    expect(screen.getByText("rolling_quantile(close, 100, 0.75)")).toBeInTheDocument();
    expect(screen.getByText("101")).toBeInTheDocument();
    // null=列不在注册表（无推导依据），与数值区分
    expect(screen.getByText("—（未在册）")).toBeInTheDocument();
    // rejected 行附拒绝原因
    expect(screen.getByText(/词表白名单校验未通过/)).toBeInTheDocument();
  });

  it("B2 模型插件三态：pending/valid/rejected 徽标+拒绝原因呈现", async () => {
    setup();
    const model = await screen.findByTestId("model-list");
    // 三态徽标限 model 区断言（formula 区 valid 徽标同文案）
    expect(within(model).getByText("训练中")).toBeInTheDocument();
    expect(within(model).getByText("有效")).toBeInTheDocument();
    expect(within(model).getByText("已拒绝")).toBeInTheDocument();
    expect(within(model).getByText(/拒绝原因：训练超时/)).toBeInTheDocument();
    // oos_summary 键值呈现
    expect(within(model).getByText(/auc: 0.61/)).toBeInTheDocument();
  });

  it("B3 词表卡：四族各一卡+词 mono+caliber_note 原文披露", async () => {
    setup();
    expect(await screen.findByTestId("vocabulary-families")).toBeInTheDocument();
    for (const family of ["时序变换", "时序结构", "逐元素", "数学词汇"]) {
      expect(screen.getByTestId(`vocabulary-family-${family}`)).toBeInTheDocument();
    }
    // 词呈现（mono code 元素）
    expect(screen.getByText("crossover")).toBeInTheDocument();
    expect(screen.getByText("kalman")).toBeInTheDocument();
    // caliber_note 原文（不改写）
    const note = screen.getByTestId("vocabulary-caliber-note");
    expect(note.textContent).toContain(VOCAB.caliber_note);
    expect(screen.getByText(/共 26 词/)).toBeInTheDocument();
  });

  it("B4 四层池：静态名单照抄+动态计数=非 builtin 插件条目+来源标注", async () => {
    setup();
    expect(await screen.findByTestId("pools-grid")).toBeInTheDocument();
    // 默认池 9 项照抄呈现（抽两个代表项）
    const defaultPool = screen.getByTestId("pool-默认池");
    expect(defaultPool.textContent).toContain("FundingPressure");
    expect(defaultPool.textContent).toContain("series 结构线（限顺耦合）");
    // 黑名单 8 项
    const blacklist = screen.getByTestId("pool-黑名单");
    expect(blacklist.textContent).toContain("trail 2%");
    expect(blacklist.textContent).toContain("裸深度网络");
    // 观察池 4 项
    expect(screen.getByTestId("pool-观察池").textContent).toContain("GARCH");
    // 静态投影来源标注
    expect(screen.getByText(/名单来源：研究需求文档（静态投影）/)).toBeInTheDocument();
    // fe-asset-data-domain 5.2：静态快照标识常显（t3r5 G5——R5 可判别锚：
    // 不冒充真数据，真值端点待建披露在场）
    const badge = screen.getByTestId("static-snapshot-badge");
    expect(badge.textContent).toContain("静态快照");
    expect(badge.textContent).toContain("be-plugin-pool-authority");
    // 动态数=插件条目（formula 1+model 1=2，builtin 1 除外）
    expect(await screen.findByTestId("pools-registered-count")).toHaveTextContent("已注册插件列 2");
  });

  it("B5 协议要点：五条静态要点呈现", async () => {
    setup();
    expect(await screen.findByTestId("plugins-protocol")).toBeInTheDocument();
    for (const t of ["静态校验三关", "版本化留档", "rejected 可查", "词表白名单", "审计现算不落库"]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
  });

  it("三态：error 呈现重试、empty 呈现空态", async () => {
    vi.mocked(svc.getFormulaPlugins).mockRejectedValue(new Error("network") as never);
    vi.mocked(svc.getModelPlugins).mockResolvedValue({ items: [], total: 0 } as never);
    setup();
    // formula 区 error 态：重试按钮（antd 中文两字按钮自动插空格「重 试」）
    expect(await screen.findByText("加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重\s?试/ })).toBeInTheDocument();
    // model 区 empty 态
    expect(await screen.findByText("暂无模型插件")).toBeInTheDocument();
    // formula 表不呈现（error 态无表）
    expect(screen.queryByTestId("formula-table")).toBeNull();
  });
});
