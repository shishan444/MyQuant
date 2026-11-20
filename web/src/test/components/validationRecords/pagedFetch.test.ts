/**
 * 分页聚合循环器单测（fix-replay-presentation-pipeline 任务 1.1/3.1）。
 *
 * 契约：页大小=端点声明上限 500；按「页未满或 has_more=false」终止；
 * 聚合保序（后端保证页内 bar_time+seq 升序——到达序拼接即全局序）；
 * 任页失败整体抛错（useQuery isError 消费，不部分呈现——半量数据+
 * 吞错是「恒 422 伪装空态」缺陷族的根因教训）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DETAIL_PAGE_SIZE, pagedFetch, paperDetailUrl,
} from "@/components/validationRecords/pagedFetch";

const okPage = (body: unknown): Response => ({
  ok: true, status: 200,
  json: () => Promise.resolve(body),
} as Response);
/** 失败形：json 故意 reject——失败路径不应读取响应体（先判 ok） */
const failPage = (status: number): Response => ({
  ok: false, status,
  json: () => Promise.reject(new Error("失败响应不应读取 body")),
} as Response);

const offsetOf = (url: string): number =>
  Number(/offset=(\d+)/.exec(url)?.[1] ?? 0);

describe("分页聚合循环器（pagedFetch——fix-replay-presentation-pipeline）", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("两页 500+97 → 597 全量聚合且保序（到达序拼接=全局序）", async () => {
    const mk = (from: number, n: number) =>
      Array.from({ length: n }, (_, i) => ({ seq: from + i }));
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(okPage({
        fills: offsetOf(url) === 0 ? mk(0, 500) : mk(500, 97),
        limit: DETAIL_PAGE_SIZE, offset: offsetOf(url),
        has_more: offsetOf(url) === 0,
      })));
    const all = await pagedFetch<{ seq: number }>({
      url: (offset, limit) =>
        `/api/paper-trading/tasks/t1/fills?limit=${limit}&offset=${offset}`,
      itemsKey: "fills",
    });
    expect(all).toHaveLength(597);
    expect(all.map((x) => x.seq)).toEqual(mk(0, 597).map((x) => x.seq));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("页未满终止：3 条且 has_more=true 也不再续页（页未满是独立终止条件）", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(okPage({
        fills: [{ seq: 1 }, { seq: 2 }, { seq: 3 }], has_more: true })));
    const all = await pagedFetch({
      url: (o) => `/x?offset=${o}`, itemsKey: "fills" });
    expect(all).toHaveLength(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("页满+has_more=false 终止：单页 500 即止", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(okPage({
        fills: Array.from({ length: 500 }, (_, i) => ({ seq: i })),
        has_more: false })));
    const all = await pagedFetch({
      url: (o) => `/x?offset=${o}`, itemsKey: "fills" });
    expect(all).toHaveLength(500);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("任页失败整体抛错（第二页 503）：不产出部分聚合", async () => {
    fetchMock.mockImplementation((url: string) =>
      offsetOf(url) === 0
        ? Promise.resolve(okPage({
            fills: Array.from({ length: 500 }, (_, i) => ({ seq: i })),
            has_more: true }))
        : Promise.resolve(failPage(503)));
    await expect(pagedFetch({
      url: (o) => `/x?offset=${o}`, itemsKey: "fills" }))
      .rejects.toThrow(/503/);
  });

  it("422 校验拒绝形抛错不吞（恒 422 伪装空态缺陷族锚——FE-1 先红锚）", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(failPage(422)));
    await expect(pagedFetch({
      url: (o) => `/x?limit=10000&offset=${o}`, itemsKey: "fills" }))
      .rejects.toThrow(/422/);
  });

  it("equity 通道接口预留：itemsKey=snapshots 同一实现（C4 复用锚）", async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(okPage({
        snapshots: offsetOf(url) === 0
          ? Array.from({ length: 500 }, (_, i) => ({ equity: i }))
          : [{ equity: 500 }],
        has_more: offsetOf(url) === 0,
      })));
    const all = await pagedFetch<{ equity: number }>({
      url: (o) => `/api/paper-trading/tasks/t1/equity?limit=500&offset=${o}`,
      itemsKey: "snapshots" });
    expect(all).toHaveLength(501);
  });

  it("paperDetailUrl：通道+task_id 转义+limit/offset 查询面", () => {
    expect(paperDetailUrl("fills", "t/1", 500, 500)).toBe(
      "/api/paper-trading/tasks/t%2F1/fills?limit=500&offset=500");
    expect(paperDetailUrl("signals", "abc", 0)).toBe(
      "/api/paper-trading/tasks/abc/signals?limit=500&offset=0");
    expect(paperDetailUrl("equity", "abc", 500)).toBe(
      "/api/paper-trading/tasks/abc/equity?limit=500&offset=500");
  });
});
