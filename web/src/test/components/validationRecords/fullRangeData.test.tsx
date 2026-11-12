/**
 * 全区间装载与 equity 拼接单测（upgrade-vr-fullrange-chart 契约 A/C）：
 * 四态判别各路径（error→truncated→mismatch→ok 判定序）/按份对账三型
 * 构造（isolated 单份退化/staged 相接边界两份各计一次/random 窗缝不参
 * 与对账）/去重不变量/mismatch 份级定位/buildEquitySeries zip+降级+
 * 段界断点+perExecEquityPoints 同源一致。服务层 mock（零外网红线）。
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/services/datasets", () => ({ getOhlcvBySymbol: vi.fn() }));

import { getOhlcvBySymbol } from "@/services/datasets";
import type { VrExecution } from "@/services/validationRecords";
import {
  buildEquitySeries,
  perExecEquityPoints,
  useFullRangeKline,
} from "@/components/validationRecords/fullRangeData";
import { toTime } from "@/components/charts/core/time";

const mockOhlcv = vi.mocked(getOhlcvBySymbol);

/** 4h bar 生成器（epoch 对齐；ISO 带 Z——OHLCV 端点实形 +00:00 等价换算） */
function bars4h(fromIso: string, n: number) {
  const start = Date.parse(fromIso) / 1000;
  return Array.from({ length: n }, (_, i) => ({
    timestamp: new Date((start + i * 14400) * 1000).toISOString(),
    open: 100, high: 110, low: 90, close: 105,
  }));
}

function makeExec(over: Partial<VrExecution> = {}): VrExecution {
  return {
    exec_ref: "main",
    exec_index: 0,
    range_start: "2025-11-04T00:00:00+00:00",
    range_end: "2025-11-10T00:00:00+00:00",
    trades: [],
    equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 0, timestamps: null, values: [] },
    trail_series: null,
    trace_rows: [],
    ...over,
  };
}

function ui(execs: VrExecution[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useFullRangeKline(execs, "BTCUSDT", "4h"), { wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useFullRangeKline 四态判别（判定序 error→truncated→mismatch→ok）", () => {
  it("ok 态：按份对账全过（staged 相接边界 bar 两份按份过滤各计一次各自吻合）", async () => {
    // 三段 4h：seg1 6 根（11-04T00~11-04T20）、seg2 6 根（11-05T00~11-05T20）、
    // seg3 6 根——段界背靠背（上段末+4h=下段首），无重叠
    const bars = [
      ...bars4h("2025-11-04T00:00:00Z", 6),
      ...bars4h("2025-11-05T00:00:00Z", 6),
      ...bars4h("2025-11-06T00:00:00Z", 6),
    ];
    const execs = [
      makeExec({ exec_ref: "趋势", range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 6, timestamps: null, values: Array.from({ length: 6 }, (_, i) => 100 + i) } }),
      makeExec({ exec_ref: "过渡", range_start: "2025-11-05T00:00:00+00:00", range_end: "2025-11-05T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-05T00:00:00+00:00", length: 6, timestamps: null, values: Array.from({ length: 6 }, (_, i) => 200 + i) } }),
      makeExec({ exec_ref: "震荡", range_start: "2025-11-06T00:00:00+00:00", range_end: "2025-11-06T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-06T00:00:00+00:00", length: 6, timestamps: null, values: Array.from({ length: 6 }, (_, i) => 300 + i) } }),
    ];
    mockOhlcv.mockResolvedValue({ data: bars } as never);
    const { result } = ui(execs);
    await waitFor(() => expect(result.current.state).not.toBe("loading"));
    expect(result.current.state).toBe("ok");
    if (result.current.state === "ok") {
      expect(result.current.candles).toHaveLength(18);
      expect(result.current.execAligned).toEqual({ 趋势: true, 过渡: true, 震荡: true });
    }
  });

  it("truncated 态：尾 bar 未达 max(range_end)——candles 照常返回照画+缺失根数", async () => {
    // 区间应到 11-10，数据只到 11-04 末（6 根）→ 缺 4 天=24 根
    const execs = [makeExec({ equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 6, timestamps: null, values: [1, 2, 3, 4, 5, 6] } })];
    mockOhlcv.mockResolvedValue({ data: bars4h("2025-11-04T00:00:00Z", 6) } as never);
    const { result } = ui(execs);
    await waitFor(() => expect(result.current.state).not.toBe("loading"));
    expect(result.current.state).toBe("truncated");
    if (result.current.state === "truncated") {
      expect(result.current.candles).toHaveLength(6);
      expect(result.current.truncatedMissing).toBe(31);
    }
  });

  it("mismatch 态：份级定位（不等份标记，相等份仍标 true）+判定序后于 truncated", async () => {
    // 份1 对账相等（6==6）、份2 不等（6 根区间 vs 5 点）——但先命中 truncated
    // （尾 bar 未达 maxEnd）；把份2 区间缩到数据内再验 mismatch
    const execs = [
      makeExec({ exec_ref: "份1", range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 6, timestamps: null, values: [1, 2, 3, 4, 5, 6] } }),
      makeExec({ exec_ref: "份2", range_start: "2025-11-05T00:00:00+00:00", range_end: "2025-11-05T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-05T00:00:00+00:00", length: 5, timestamps: null, values: [1, 2, 3, 4, 5] } }),
    ];
    const bars = [...bars4h("2025-11-04T00:00:00Z", 6), ...bars4h("2025-11-05T00:00:00Z", 6)];
    mockOhlcv.mockResolvedValue({ data: bars } as never);
    const { result } = ui(execs);
    await waitFor(() => expect(result.current.state).not.toBe("loading"));
    expect(result.current.state).toBe("mismatch");
    if (result.current.state === "mismatch") {
      expect(result.current.mismatchedExecs).toEqual([{ exec_ref: "份2", bars: 6, points: 5 }]);
      expect(result.current.execAligned).toEqual({ 份1: true, 份2: false });
    }
  });

  it("error 态：端点失败沿现有降级（判定序最前）", async () => {
    mockOhlcv.mockRejectedValue(new Error("boom"));
    const { result } = ui([makeExec()]);
    await waitFor(() => expect(result.current.state).not.toBe("loading"));
    expect(result.current.state).toBe("error");
  });

  it("去重不变量：合并输出每时间戳恰一根 bar+epoch 升序（random 窗缝拓扑）", async () => {
    // 两窗不重叠有缝：窗1 11-04 前 3 根、窗2 11-06 前 3 根（11-05 全天缝隙）
    const bars = [
      ...bars4h("2025-11-04T00:00:00Z", 3),
      ...bars4h("2025-11-06T00:00:00Z", 3),
      // 模拟服务端重复（不同 OHLC 同 timestamp）——去重保序取后者
      { timestamp: bars4h("2025-11-06T00:00:00Z", 3)[0].timestamp, open: 1, high: 1, low: 1, close: 1 },
    ];
    const execs = [
      makeExec({ exec_ref: "w1", range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T08:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 3, timestamps: null, values: [1, 2, 3] } }),
      makeExec({ exec_ref: "w2", range_start: "2025-11-06T00:00:00+00:00", range_end: "2025-11-06T08:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-06T00:00:00+00:00", length: 3, timestamps: null, values: [4, 5, 6] } }),
    ];
    mockOhlcv.mockResolvedValue({ data: bars } as never);
    const { result } = ui(execs);
    await waitFor(() => expect(result.current.state).not.toBe("loading"));
    expect(result.current.state).toBe("ok");
    if (result.current.state === "ok") {
      // 6 根（7 条输入去重后）+缝隙 bar 不参与对账（份对账按闭端过滤各 3 根吻合）
      expect(result.current.candles).toHaveLength(6);
      const ts = result.current.candles.map((b) => toTime(b.timestamp) as number);
      expect([...ts].sort((a, b) => a - b)).toEqual(ts);
      expect(result.current.execAligned).toEqual({ w1: true, w2: true });
    }
  });

  it("isolated 单份退化：原等式即按份对账（N==N）", async () => {
    const execs = [makeExec({ range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
      equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 6, timestamps: null, values: [1, 2, 3, 4, 5, 6] } })];
    mockOhlcv.mockResolvedValue({ data: bars4h("2025-11-04T00:00:00Z", 6) } as never);
    const { result } = ui(execs);
    await waitFor(() => expect(result.current.state).not.toBe("loading"));
    expect(result.current.state).toBe("ok");
  });
});

describe("buildEquitySeries/perExecEquityPoints（契约 C）", () => {
  const bars = [...bars4h("2025-11-04T00:00:00Z", 6), ...bars4h("2025-11-05T00:00:00Z", 6)];

  it("zip 主路径：equity 锚到该份区间真实 bar 时间戳（物理同源）", () => {
    const exec = makeExec({ range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
      equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 6, timestamps: null, values: [10, 20, 30, 40, 50, 60] } });
    const pts = perExecEquityPoints(exec, bars);
    expect(pts.map((p) => p.value)).toEqual([10, 20, 30, 40, 50, 60]);
    expect(pts[0].time).toBe(bars[0].timestamp);
    expect(pts[5].time).toBe(bars[5].timestamp);
  });

  it("份级降级（mismatch 不等份）：timestamps 直用→compact 等距重建，时间产出 ISO 带 Z", () => {
    // 不等份（bars 6 vs values 3）+timestamps 直用
    const e1 = makeExec({ range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
      equity: { grid_aligned: false, timeframe: "4h", start: null, length: 3,
        timestamps: ["2025-11-04T00:00:00+00:00", "2025-11-04T04:00:00+00:00", "2025-11-04T08:00:00+00:00"], values: [1, 2, 3] } });
    const pts1 = perExecEquityPoints(e1, bars);
    expect(pts1.map((p) => p.time)).toEqual([
      "2025-11-04T00:00:00.000Z", "2025-11-04T04:00:00.000Z", "2025-11-04T08:00:00.000Z",
    ]);
    // compact 等距重建（grid_aligned=true 无 timestamps）
    const e2 = makeExec({ range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
      equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04 00:00:00+00:00", length: 3, timestamps: null, values: [7, 8, 9] } });
    const pts2 = perExecEquityPoints(e2, bars);
    expect(pts2.map((p) => p.time)).toEqual([
      "2025-11-04T00:00:00.000Z", "2025-11-04T04:00:00.000Z", "2025-11-04T08:00:00.000Z",
    ]);
  });

  it("多份拼接：段界 whitespace 断点（无 value 项，时间严格介于两段之间）+空份跳过", () => {
    const execs = [
      makeExec({ exec_ref: "s1", range_start: "2025-11-04T00:00:00+00:00", range_end: "2025-11-04T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-04T00:00:00+00:00", length: 6, timestamps: null, values: [1, 2, 3, 4, 5, 6] } }),
      makeExec({ exec_ref: "空份", range_start: "2025-11-06T00:00:00+00:00", range_end: "2025-11-06T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: null, length: 0, timestamps: null, values: [] } }),
      makeExec({ exec_ref: "s2", range_start: "2025-11-05T00:00:00+00:00", range_end: "2025-11-05T20:00:00+00:00",
        equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-05T00:00:00+00:00", length: 6, timestamps: null, values: [7, 8, 9, 10, 11, 12] } }),
    ];
    const out = buildEquitySeries(execs, bars);
    // 空份无点无断点贡献；s1 6 点+断点+s2 6 点=13 项
    expect(out).toHaveLength(13);
    const brk = out.find((p) => !("value" in p) || p.value === undefined);
    expect(brk).toBeDefined();
    const brkEpoch = toTime((brk as { time: string }).time) as number;
    const s1Last = toTime(bars[5].timestamp) as number;
    const s2First = toTime(bars[6].timestamp) as number;
    expect(brkEpoch).toBeGreaterThan(s1Last);
    expect(brkEpoch).toBeLessThan(s2First);
    // 全空→[]
    expect(buildEquitySeries([execs[1]], bars)).toEqual([]);
  });

  it("perExecEquityPoints 与 buildEquitySeries 同份输出逐位一致（同源同锚）", () => {
    const exec = makeExec({ exec_ref: "s1", range_start: "2025-11-05T00:00:00+00:00", range_end: "2025-11-05T20:00:00+00:00",
      equity: { grid_aligned: true, timeframe: "4h", start: "2025-11-05T00:00:00+00:00", length: 6, timestamps: null, values: [7, 8, 9, 10, 11, 12] } });
    const single = perExecEquityPoints(exec, bars);
    const built = buildEquitySeries([exec], bars).filter((p) => "value" in p);
    expect(built).toEqual(single);
  });
});
