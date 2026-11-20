/**
 * 分页聚合循环器单测（fix-replay-presentation-pipeline 任务 1.1/3.1）。
 *
 * 契约：页大小=端点声明上限 500；按「页未满或 hasMore=false」终止；
 * 聚合保序（后端保证页内 bar_time+seq 升序——到达序拼接即全局序）；
 * 任页失败整体抛错（useQuery isError 消费，不部分呈现——半量数据+
 * 吞错是「恒 422 伪装空态」缺陷族的根因教训）。
 * mock 面（CARD-01 注入形）：fetchPage 以 vi.fn 注入（传输通道归服务
 * 层，不再 stubGlobal fetch）；失败形按服务层真实错误形态仿真 ApiError
 * （kind/status——中文化 message 通道）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DETAIL_PAGE_SIZE, pagedFetch,
} from "@/components/validationRecords/pagedFetch";
import { ApiError } from "@/services/api";

describe("分页聚合循环器（pagedFetch——fix-replay-presentation-pipeline）", () => {
  const pageMock = vi.fn();
  beforeEach(() => pageMock.mockReset());

  it("两页 500+97 → 597 全量聚合且保序（到达序拼接=全局序）", async () => {
    const mk = (from: number, n: number) =>
      Array.from({ length: n }, (_, i) => ({ seq: from + i }));
    pageMock.mockImplementation((offset: number) =>
      Promise.resolve({
        items: offset === 0 ? mk(0, 500) : mk(500, 97),
        hasMore: offset === 0,
      }));
    const all = await pagedFetch<{ seq: number }>({ fetchPage: pageMock });
    expect(all).toHaveLength(597);
    expect(all.map((x) => x.seq)).toEqual(mk(0, 597).map((x) => x.seq));
    expect(pageMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    { pages: [500, 500, 97], calls: 3 },
    { pages: [500, 500, 500, 3], calls: 4 },
    { pages: [500, 1], calls: 2 },
  ])("多页切分 $pages → 聚合保序全量（属性：任意页切分输出=拼接）",
    async ({ pages, calls }: { pages: number[]; calls: number }) => {
      let seq = 0;
      pageMock.mockImplementation((offset: number) => {
        const idx = offset / DETAIL_PAGE_SIZE;
        const n = pages[idx] ?? 0;
        return Promise.resolve({
          items: Array.from({ length: n }, () => ({ seq: seq++ })),
          hasMore: idx + 1 < pages.length,
        });
      });
      const all = await pagedFetch<{ seq: number }>({ fetchPage: pageMock });
      expect(all).toHaveLength(pages.reduce((a, b) => a + b, 0));
      // 保序不变量：输出全局递增序（到达序拼接=全局序的属性化表达）
      expect(all.map((x) => x.seq)).toEqual(
        Array.from({ length: all.length }, (_, i) => i));
      expect(pageMock).toHaveBeenCalledTimes(calls);
    });

  it("页未满终止：3 条且 hasMore=true 也不再续页（页未满是独立终止条件）", async () => {
    pageMock.mockResolvedValue({
      items: [{ seq: 1 }, { seq: 2 }, { seq: 3 }], hasMore: true });
    const all = await pagedFetch({ fetchPage: pageMock });
    expect(all).toHaveLength(3);
    expect(pageMock).toHaveBeenCalledTimes(1);
  });

  it("页满+hasMore=false 终止：单页 500 即止", async () => {
    pageMock.mockResolvedValue({
      items: Array.from({ length: 500 }, (_, i) => ({ seq: i })),
      hasMore: false });
    const all = await pagedFetch({ fetchPage: pageMock });
    expect(all).toHaveLength(500);
    expect(pageMock).toHaveBeenCalledTimes(1);
  });

  it("hasMore 缺失（undefined 形）：满页即终止不续页（契约终止条件 ≠true 边界）", async () => {
    pageMock.mockResolvedValue({
      items: Array.from(
        { length: DETAIL_PAGE_SIZE }, (_, i) => ({ seq: i })) });
    const all = await pagedFetch({ fetchPage: pageMock });
    expect(all).toHaveLength(500);
    expect(pageMock).toHaveBeenCalledTimes(1);
  });

  // 失败注入用手写闭包（vitest 4 直测场景下 vi.fn 产生 rejection
  //  会触发 spy 追踪链 unhandled rejection——错误不经 mock 中转）
  it("任页失败整体抛错（第二页 503）：不产出部分聚合", async () => {
    await expect(pagedFetch({
      fetchPage: (offset: number) =>
        offset === 0
          ? Promise.resolve({
              items: Array.from({ length: 500 }, (_, i) => ({ seq: i })),
              hasMore: true })
          : Promise.reject(new ApiError(
              "server", "服务暂不可用，请稍后重试", 503)),
    })).rejects.toThrow(/服务暂不可用/);
  });

  it("422 校验拒绝形抛错不吞（恒 422 伪装空态缺陷族锚——FE-1 先红锚）", async () => {
    await expect(pagedFetch({
      fetchPage: () => Promise.reject(
        new ApiError("validation", "请求参数有误", 422)),
    })).rejects.toThrow(/请求参数有误/);
  });

  it("网络错形（fetch 本身 reject）：整体抛错不吞（design §4 失败形族"
      + "——非 HTTP 状态形的第三形）", async () => {
    await expect(pagedFetch({
      fetchPage: () => Promise.reject(
        new ApiError("network", "网络连接失败，请检查网络后重试")),
    })).rejects.toThrow(/网络连接失败/);
  });

  it("熔断防线：恒满页+恒 has_more → 超熔断页数抛错"
      + "（防病态续页死循环——分页契约防御条款）", async () => {
    const fullPage = Array.from(
      { length: DETAIL_PAGE_SIZE }, (_, i) => ({ seq: i }));
    pageMock.mockResolvedValue({ items: fullPage, hasMore: true });
    await expect(pagedFetch({ fetchPage: pageMock }))
      .rejects.toThrow(/熔断页数/);
    expect(pageMock).toHaveBeenCalledTimes(1000);
  });

  it("signal 透传：options.signal 逐页传给 fetchPage（取消链——CARD-01"
      + " 修复行为面）；缺省不注入", async () => {
    const ac = new AbortController();
    pageMock.mockImplementation((offset: number) =>
      Promise.resolve({
        items: offset === 0
          ? Array.from(
              { length: DETAIL_PAGE_SIZE }, (_, i) => ({ seq: i }))
          : [{ seq: 500 }],
        hasMore: offset === 0,
      }));
    await pagedFetch({ fetchPage: pageMock, signal: ac.signal });
    // 两页逐页同传同一实例（翻页不丢取消链）
    expect(pageMock).toHaveBeenNthCalledWith(1, 0, ac.signal);
    expect(pageMock).toHaveBeenNthCalledWith(2, 500, ac.signal);
    // 缺省面：不注入 signal → 第二参 undefined（默认不打扰）
    pageMock.mockReset();
    pageMock.mockResolvedValue({ items: [], hasMore: false });
    await pagedFetch({ fetchPage: pageMock });
    expect(pageMock).toHaveBeenCalledWith(0, undefined);
  });

  it("equity 通道接口预留：fetchPage 注入任意 T 同一实现（C4 复用锚）", async () => {
    pageMock.mockImplementation((offset: number) =>
      Promise.resolve({
        items: offset === 0
          ? Array.from({ length: 500 }, (_, i) => ({ equity: i }))
          : [{ equity: 500 }],
        hasMore: offset === 0,
      }));
    const all = await pagedFetch<{ equity: number }>({ fetchPage: pageMock });
    expect(all).toHaveLength(501);
  });
});
