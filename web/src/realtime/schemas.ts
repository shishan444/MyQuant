/**
 * 实时事件 zod schema 本体——契约见 ./contract.md（G2 冻结，后端 B8 实现依据）。
 *
 * 校验纪律：结构性防御（type 判别、字段存在与基本类型），不复制后端模型内部结构
 * （champion_dna/batch_results 等嵌套体以 record 放行——结构权威=后端，全量复刻
 * 会在后端模型演化时误杀合法消息）。
 */
import { z } from "zod";

// ---------------------------------------------------------------------------
// 连接层协议消息（适配层消化，禁入业务缓存）
// ---------------------------------------------------------------------------

const SubscribedMessage = z.object({
  type: z.literal("subscribed"),
  task_id: z.string(),
});

const PongMessage = z.object({
  type: z.literal("pong"),
});

/** 协议消息判别：命中则消息归连接层，业务层不可见 */
export const ProtocolMessage = z.discriminatedUnion("type", [SubscribedMessage, PongMessage]);

// ---------------------------------------------------------------------------
// trading 通道业务事件（模拟交易域七事件——payload 实证：core/paper_trading/
// runtime/executor.py _push 构造点 + api/realtime.py _emit_task_snapshot）
// ---------------------------------------------------------------------------

const TradingTaskStarted = z.object({
  type: z.literal("task_started"),
  task_id: z.string(),
}).strict();

/** connect 快照（api/realtime.py /trading 分支：任务存在即发，五态自解释） */
const TradingTaskSnapshot = z.object({
  type: z.literal("task_snapshot"),
  task_id: z.string(),
  status: z.string(),
  equity: z.number().nullable(),
  balance: z.number().nullable(),
  total_trades: z.number(),
}).strict();

/** 每 bar 落库后推进推送（executor._persist_bar） */
const TradingTaskUpdate = z.object({
  type: z.literal("task_update"),
  task_id: z.string(),
  status: z.string(),
  equity: z.number(),
  balance: z.number(),
  unrealized_pnl: z.number(),
  bars_processed: z.number(),
  window_bars: z.number().nullable(),
}).strict();

/** 逐笔成交推送（executor._execute；side/price 等出场类可空） */
const TradingFillEvent = z.object({
  type: z.literal("fill_event"),
  task_id: z.string(),
  bar_time: z.string(),
  seq: z.number(),
  kind: z.string(),
  side: z.string().nullable(),
  price: z.number().nullable(),
  quantity: z.number().nullable(),
  fee: z.number().nullable(),
  realized_pnl: z.number().nullable(),
  reason: z.string().nullable(),
}).strict();

const TradingTaskCompleted = z.object({
  type: z.literal("task_completed"),
  task_id: z.string(),
  final_equity: z.number(),
  total_trades: z.number(),
}).strict();

/** stop_reason 键恒在；当前全部调用路径为字符串（user_stop/stop_condition——
 *  executor._finalize("stopped") 四处调用均显式传串），schema nullable 为宽容超集 */
const TradingTaskStopped = z.object({
  type: z.literal("task_stopped"),
  task_id: z.string(),
  stop_reason: z.string().nullable(),
}).strict();

const TradingTaskError = z.object({
  type: z.literal("task_error"),
  task_id: z.string(),
  error_detail: z.string(),
}).strict();

export const TradingEvent = z.discriminatedUnion("type", [
  TradingTaskStarted,
  TradingTaskSnapshot,
  TradingTaskUpdate,
  TradingFillEvent,
  TradingTaskCompleted,
  TradingTaskStopped,
  TradingTaskError,
]);

export type TradingEvent = z.infer<typeof TradingEvent>;

// ---------------------------------------------------------------------------
// 校验入口：失败丢弃留痕（channel + 原因进 console.warn 观测通道）
// ---------------------------------------------------------------------------

export type ValidatedMessage<TEvent> =
  | { kind: "protocol" }
  | { kind: "event"; event: TEvent }
  | { kind: "invalid" };

function validate<T>(channel: string, schema: z.ZodType<T>, raw: unknown): ValidatedMessage<T> {
  if (ProtocolMessage.safeParse(raw).success) {
    return { kind: "protocol" };
  }
  const parsed = schema.safeParse(raw);
  if (parsed.success) {
    return { kind: "event", event: parsed.data };
  }
  const typeHint =
    typeof raw === "object" && raw !== null && "type" in raw
      ? String((raw as { type: unknown }).type)
      : "non-object";
  console.warn(`[realtime] ${channel}: 消息校验失败已丢弃（type=${typeHint}）`, {
    issues: parsed.error.issues.slice(0, 3).map((i) => `${i.path.join(".")}: ${i.message}`),
  });
  return { kind: "invalid" };
}

export function validateTradingMessage(raw: unknown): ValidatedMessage<TradingEvent> {
  return validate("trading", TradingEvent, raw);
}
