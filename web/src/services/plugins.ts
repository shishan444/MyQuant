import { api } from "@/services/api";

/**
 * 11 插件与组件库服务层（alignment-batch D6）。
 * 四端点全只读：公式插件审计/模型插件审计/研究词表/可用信号列。
 * 响应为裸 dict 无 schema（routes 层裸 Dict 返回），按后端实际 JSON 形状手写。
 */

/** 公式插件行 ← GET /api/research/plugins/formula（formula_plugins 表行+审计两现算字段） */
export interface FormulaPluginRow {
  plugin_id: string;
  name: string;
  version: number;
  expression: string;
  /** 后端已反序列化为数组（路由层 json.loads）；异常时保持字符串 */
  output_columns: string[] | string;
  /** valid | rejected（rejected 只留记录不注册） */
  status: string;
  reject_reason?: string | null;
  strategy_visible: number;
  created_at: string;
  /** 审计现算字段（alignment-batch β）：null=列不在注册表，无推导依据 */
  warmup_bars: number | null;
  dependency_closure: string[];
}

/** 模型插件样本外摘要（oos_summary JSON 反序列化后；键视模板而定，消费端宽松） */
export type ModelOosSummary = Record<string, number | string | null>;

/** 模型插件行 ← GET /api/research/plugins/model（model_plugins 表行） */
export interface ModelPluginRow {
  plugin_id: string;
  name: string;
  version: number;
  /** JSON 字符串（配置快照：features/template/params/target 等） */
  config: string;
  signal_column: string;
  /** pending | valid | rejected（异步训练：failed/超时 → rejected+reason） */
  status: string;
  reject_reason?: string | null;
  oos_summary?: ModelOosSummary | string | null;
  strategy_visible: number;
  created_at: string;
}

/** 词表响应 ← GET /api/research/vocabulary（四族中文键：时序变换/时序结构/逐元素/数学词汇） */
export interface VocabularyResponse {
  total: number;
  families: Record<string, string[]>;
  counts: Record<string, number>;
  /** 双口径披露文案（原文呈现，不改写） */
  caliber_note: string;
}

/** 信号列条目 ← GET /api/research/signals（内置∪插件运行时合并） */
export interface SignalColumnEntry {
  column_key: string;
  output_fields: string[];
  /** builtin | formula | model（插件条目计数=非 builtin 条目） */
  source: string;
  category: string;
  lookback: number | null;
  strategy_visible: boolean;
  version: number | null;
  scopes: string[];
  reference_form: Record<string, unknown>;
}

export async function getFormulaPlugins(): Promise<{ items: FormulaPluginRow[]; total: number }> {
  return api.get("/api/research/plugins/formula");
}

export async function getModelPlugins(): Promise<{ items: ModelPluginRow[]; total: number }> {
  return api.get("/api/research/plugins/model");
}

export async function getVocabulary(): Promise<VocabularyResponse> {
  return api.get("/api/research/vocabulary");
}

export async function getSignalColumns(): Promise<SignalColumnEntry[]> {
  return api.get("/api/research/signals");
}
