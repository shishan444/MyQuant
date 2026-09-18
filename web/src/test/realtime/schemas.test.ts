/**
 * 实时事件 schema L1 直测——契约（src/realtime/contract.md）的枚举推导：
 * 每事件一正例 + 缺字段/类型错误拒绝 + 协议消息分流 + 非对象拒绝。
 */
import { describe, it, expect, vi } from "vitest";
import {
  EvolutionEvent,
  TradingEvent,
  validateEvolutionMessage,
  validateTradingMessage,
} from "@/realtime/schemas";

const validEvolutionEvents = [
  { type: "task_started", task_id: "t1", status: "running", target_score: 80, max_generations: 200 },
  { type: "task_snapshot", task_id: "t1", status: "running", current_generation: 3, best_score: 85, target_score: 80, max_generations: 200, current_phase: "evolving" },
  { type: "phase_changed", task_id: "t1", phase: "initializing" },
  { type: "population_started", task_id: "t1", population_count: 2, best_score_ever: 85, total_generations_so_far: 40 },
  { type: "strategy_discovered", task_id: "t1", strategy_id: "s1", score: 82, fitness: 82, qualified: true, name: null, generation: 10 },
  { type: "generation_complete", task_id: "t1", generation: 5, best_score: 84, best_fitness: 84, avg_score: 70, target_score: 80, max_generations: 200, qualified_count: 2, champion_dna: { signal_genes: [] } },
  { type: "evolution_complete", task_id: "t1", stop_reason: "target", total_generations: 42, champion_score: 88, generation: 42, best_score: 88, best_fitness: 88, target_score: 80, max_generations: 200 },
];

const validTradingEvents = [
  { type: "task_started", task_id: "t1" },
  { type: "task_snapshot", task_id: "t1", status: "running", position_side: "long", balance: 100000, total_trades: 3, total_pnl: 120 },
  { type: "position_update", task_id: "t1", position: { side: "long", entry_price: 50000, quantity: 0.1, margin: 5000, cumulative_funding: -1.2 }, balance: 100000, equity: 100500, unrealized_pnl: 500, total_trades: 3, total_pnl: 120 },
  { type: "position_update", task_id: "t1", position: null, balance: 100000, equity: 100000, unrealized_pnl: 0, total_trades: 0, total_pnl: 0 },
];

describe("EvolutionEvent schema L1", () => {
  it.each(validEvolutionEvents.map((e) => [e.type, e] as const))(
    "接受合法事件 %s",
    (_type, event) => {
      const parsed = EvolutionEvent.safeParse(event);
      if (!parsed.success) throw new Error(JSON.stringify(parsed.error.issues, null, 2));
      expect(parsed.success).toBe(true);
    },
  );

  it.each([
    ["task_started 缺 max_generations", { type: "task_started", task_id: "t1", status: "running", target_score: 80 }],
    ["generation_complete best_score 类型错误", { ...validEvolutionEvents[5], best_score: "84" }],
    ["generation_complete 缺 task_id", { ...validEvolutionEvents[5], task_id: undefined }],
    ["未知事件类型", { type: "mystery_event", task_id: "t1" }],
  ])("拒绝：%s", (_label, bad) => {
    expect(EvolutionEvent.safeParse(bad).success).toBe(false);
  });

  it("champion_dna 可缺省或 null（嵌套结构不校验）", () => {
    const base = validEvolutionEvents[5];
    expect(EvolutionEvent.safeParse({ ...base, champion_dna: undefined }).success).toBe(true);
    expect(EvolutionEvent.safeParse({ ...base, champion_dna: null }).success).toBe(true);
  });
});

describe("TradingEvent schema L1", () => {
  it.each(validTradingEvents.map((e) => [e.type, e] as const))(
    "接受合法事件 %s",
    (_type, event) => {
      expect(TradingEvent.safeParse(event).success).toBe(true);
    },
  );

  it.each([
    ["position_update position.qty 类型错误", { ...validTradingEvents[2], position: { side: "long", entry_price: 1, quantity: "0.1", margin: 1, cumulative_funding: 0 } }],
    ["task_snapshot 缺 total_pnl", { type: "task_snapshot", task_id: "t1", status: "running", position_side: null, balance: 1, total_trades: 0 }],
  ])("拒绝：%s", (_label, bad) => {
    expect(TradingEvent.safeParse(bad).success).toBe(false);
  });
});

describe("校验入口分流", () => {
  it("协议消息（subscribed/pong）归连接层，kind=protocol", () => {
    expect(validateEvolutionMessage({ type: "subscribed", task_id: "t1" }).kind).toBe("protocol");
    expect(validateTradingMessage({ type: "pong" }).kind).toBe("protocol");
  });

  it("合法业务事件透传（kind=event 且窄化）", () => {
    const r = validateEvolutionMessage(validEvolutionEvents[2]);
    expect(r.kind).toBe("event");
    if (r.kind === "event" && r.event.type === "phase_changed") {
      expect(r.event.phase).toBe("initializing");
    } else {
      throw new Error("应为 phase_changed 事件");
    }
  });

  it("非法消息丢弃留痕（console.warn 含通道与 type 提示）", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const r = validateTradingMessage({ type: "position_update", task_id: "t1" });
    expect(r.kind).toBe("invalid");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain("trading");
    warn.mockRestore();
  });

  it("非对象输入拒绝", () => {
    expect(validateEvolutionMessage("hello").kind).toBe("invalid");
    expect(validateEvolutionMessage(null).kind).toBe("invalid");
  });
});
