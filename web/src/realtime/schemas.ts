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
// evolution 通道业务事件（payload 实证：api/runner.py、api/routes/ws.py）
// ---------------------------------------------------------------------------

const dnaLike = z.record(z.string(), z.unknown()).nullable().optional();

const EvolutionTaskStarted = z.object({
  type: z.literal("task_started"),
  task_id: z.string(),
  status: z.string(),
  target_score: z.number(),
  max_generations: z.number(),
});

const EvolutionTaskSnapshot = z.object({
  type: z.literal("task_snapshot"),
  task_id: z.string(),
  status: z.string(),
  current_generation: z.number(),
  best_score: z.number().nullable(),
  target_score: z.number().nullable(),
  max_generations: z.number().nullable(),
  current_phase: z.string().nullable(),
});

const PhaseChanged = z.object({
  type: z.literal("phase_changed"),
  task_id: z.string(),
  phase: z.string(),
});

const PopulationStarted = z.object({
  type: z.literal("population_started"),
  task_id: z.string(),
  population_count: z.number(),
  best_score_ever: z.number(),
  total_generations_so_far: z.number(),
});

const StrategyDiscovered = z.object({
  type: z.literal("strategy_discovered"),
  task_id: z.string(),
  strategy_id: z.string(),
  score: z.number(),
  fitness: z.number(),
  qualified: z.boolean(),
  name: z.string().nullable(),
  generation: z.number(),
});

const GenerationComplete = z.object({
  type: z.literal("generation_complete"),
  task_id: z.string(),
  generation: z.number(),
  best_score: z.number(),
  best_fitness: z.number(),
  avg_score: z.number(),
  target_score: z.number(),
  max_generations: z.number(),
  qualified_count: z.number().optional(),
  champion_dna: dnaLike,
});

const EvolutionComplete = z.object({
  type: z.literal("evolution_complete"),
  task_id: z.string(),
  stop_reason: z.string(),
  total_generations: z.number(),
  champion_score: z.number(),
  generation: z.number(),
  best_score: z.number(),
  best_fitness: z.number(),
  target_score: z.number(),
  max_generations: z.number(),
});

export const EvolutionEvent = z.discriminatedUnion("type", [
  EvolutionTaskStarted,
  EvolutionTaskSnapshot,
  PhaseChanged,
  PopulationStarted,
  StrategyDiscovered,
  GenerationComplete,
  EvolutionComplete,
]);

export type EvolutionEvent = z.infer<typeof EvolutionEvent>;

// ---------------------------------------------------------------------------
// trading 通道业务事件（payload 实证：core/trading/runner.py、ws.py）
// ---------------------------------------------------------------------------

const TradingTaskStarted = z.object({
  type: z.literal("task_started"),
  task_id: z.string(),
});

const TradingTaskSnapshot = z.object({
  type: z.literal("task_snapshot"),
  task_id: z.string(),
  status: z.string(),
  position_side: z.string().nullable(),
  balance: z.number().nullable(),
  total_trades: z.number(),
  total_pnl: z.number(),
});

const PositionUpdate = z.object({
  type: z.literal("position_update"),
  task_id: z.string(),
  position: z
    .object({
      side: z.string(),
      entry_price: z.number(),
      quantity: z.number(),
      margin: z.number(),
      cumulative_funding: z.number(),
    })
    .nullable(),
  balance: z.number(),
  equity: z.number(),
  unrealized_pnl: z.number(),
  total_trades: z.number(),
  total_pnl: z.number(),
});

export const TradingEvent = z.discriminatedUnion("type", [
  TradingTaskStarted,
  TradingTaskSnapshot,
  PositionUpdate,
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

export function validateEvolutionMessage(raw: unknown): ValidatedMessage<EvolutionEvent> {
  return validate("evolution", EvolutionEvent, raw);
}

export function validateTradingMessage(raw: unknown): ValidatedMessage<TradingEvent> {
  return validate("trading", TradingEvent, raw);
}
