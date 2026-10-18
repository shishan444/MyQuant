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

/**
 * 纸盘新域七事件合法 payload（逐字段对齐契约源——core/paper_trading/
 * runtime/executor.py _push 构造点 :409/:439/:674/:812/:858/:863/:867 +
 * api/realtime.py _emit_task_snapshot /trading 分支）。
 */
const validTradingEvents = [
  { type: "task_started", task_id: "t1" },
  { type: "task_snapshot", task_id: "t1", status: "running", equity: 100500, balance: 100000, total_trades: 3 },
  { type: "task_snapshot", task_id: "t1", status: "pending", equity: null, balance: null, total_trades: 0 },
  { type: "task_update", task_id: "t1", status: "running", equity: 100500, balance: 100000, unrealized_pnl: 500, bars_processed: 42, window_bars: 180 },
  { type: "task_update", task_id: "t1", status: "running", equity: 100000, balance: 100000, unrealized_pnl: 0, bars_processed: 1, window_bars: null },
  { type: "fill_event", task_id: "t1", bar_time: "2026-10-17T08:00:00+00:00", seq: 3, kind: "open", side: "long", price: 50000, quantity: 0.1, fee: 5, realized_pnl: null, reason: "signal" },
  { type: "fill_event", task_id: "t1", bar_time: "2026-10-17T12:00:00+00:00", seq: 9, kind: "exit_sl", side: null, price: 49000, quantity: null, fee: null, realized_pnl: -100.5, reason: null },
  { type: "task_completed", task_id: "t1", final_equity: 102000, total_trades: 7 },
  { type: "task_stopped", task_id: "t1", stop_reason: "user_stop" },
  { type: "task_stopped", task_id: "t1", stop_reason: null },
  { type: "task_error", task_id: "t1", error_detail: "ValueError: bad dna" },
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

describe("TradingEvent schema L1（七事件对齐——每事件合法 payload 过+缺字段/错型拒）", () => {
  it.each(validTradingEvents.map((e) => [e.type, e] as const))(
    "接受合法事件 %s",
    (_type, event) => {
      const parsed = TradingEvent.safeParse(event);
      if (!parsed.success) throw new Error(JSON.stringify(parsed.error.issues, null, 2));
      expect(parsed.success).toBe(true);
    },
  );

  it.each([
    // ① task_started（executor.py:439）
    ["task_started 缺 task_id", { type: "task_started" }],
    ["task_started task_id 错型", { type: "task_started", task_id: 42 }],
    // ② task_snapshot（api/realtime.py /trading 分支——connect 快照）
    ["task_snapshot 缺 equity", { type: "task_snapshot", task_id: "t1", status: "running", balance: 1, total_trades: 0 }],
    ["task_snapshot equity 错型", { type: "task_snapshot", task_id: "t1", status: "running", equity: "100500", balance: null, total_trades: 0 }],
    // ③ task_update（executor.py:812）
    ["task_update 缺 bars_processed", { type: "task_update", task_id: "t1", status: "running", equity: 1, balance: 1, unrealized_pnl: 0, window_bars: null }],
    ["task_update equity 错型", { type: "task_update", task_id: "t1", status: "running", equity: "1", balance: 1, unrealized_pnl: 0, bars_processed: 1, window_bars: 180 }],
    // ④ fill_event（executor.py:674）
    ["fill_event 缺 kind", { type: "fill_event", task_id: "t1", bar_time: "t", seq: 1, side: null, price: null, quantity: null, fee: null, realized_pnl: null, reason: null }],
    ["fill_event seq 错型", { type: "fill_event", task_id: "t1", bar_time: "t", seq: "1", kind: "open", side: null, price: null, quantity: null, fee: null, realized_pnl: null, reason: null }],
    ["fill_event bar_time 错型", { ...validTradingEvents[5], bar_time: 123 }],
    // ⑤ task_completed（executor.py:858）
    ["task_completed 缺 final_equity", { type: "task_completed", task_id: "t1", total_trades: 3 }],
    ["task_completed total_trades 错型", { type: "task_completed", task_id: "t1", final_equity: 1, total_trades: "3" }],
    // ⑥ task_stopped（executor.py:863——stop_reason 可 null）
    ["task_stopped 缺 stop_reason", { type: "task_stopped", task_id: "t1" }],
    ["task_stopped stop_reason 错型", { type: "task_stopped", task_id: "t1", stop_reason: 42 }],
    // ⑦ task_error（executor.py:409 装载失败 / :867 异常终态）
    ["task_error 缺 error_detail", { type: "task_error", task_id: "t1" }],
    ["task_error error_detail 错型", { type: "task_error", task_id: "t1", error_detail: null }],
    // 旧事件退役：position_update 与旧形 task_snapshot 必须拒（discriminatedUnion 无此分支）
    ["旧事件 position_update 拒收", { type: "position_update", task_id: "t1", position: null, balance: 1, equity: 1, unrealized_pnl: 0, total_trades: 0, total_pnl: 0 }],
    ["旧形 task_snapshot（position_side 形制）拒收", { type: "task_snapshot", task_id: "t1", status: "running", position_side: "long", balance: 100000, total_trades: 3, total_pnl: 120 }],
    // .strict() 从严：多字段拒收（design §3:84「多字段少字段均红」——
    // 合法 payload 加一个多余字段即拒）
    ["task_update 多字段拒收（strict）", { ...validTradingEvents[3], extra_field: "x" }],
    ["fill_event 多字段拒收（strict）", { ...validTradingEvents[5], surprise: 1 }],
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
