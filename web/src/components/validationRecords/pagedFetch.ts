/**
 * 纸盘明细端点分页聚合装载（fix-replay-presentation-pipeline 任务 1.1）。
 *
 * 修复背景：此前前端以 limit=10000 请求纸盘明细端点，超出声明式校验
 * 上限（Query le=500）恒 422，且 `!r.ok → []` 吞错伪装「无成交」空态
 * ——回放四件套在任何真实 replay 记录上恒空（FE-1，缺陷族第二次）。
 *
 * 语义：页大小=500（端点声明上限）；按「页未满或 has_more=false」
 * 终止续页；聚合保序（后端保证页内 bar_time+seq 升序——到达序拼接
 * 即全局序）；任页失败整体抛错（useQuery isError 消费——不部分呈现，
 * 半量数据+吞错是本缺陷族的根因教训）。equity 通道经 itemsKey 泛化
 * 预留（C4 权益曲线复用同一实现——三通道一份，不写三份）。
 */

/** 页大小=端点声明上限（paper_trading.py fills/signals/equity Query le=500） */
export const DETAIL_PAGE_SIZE = 500;

/** 服务端异常（恒满页+恒 has_more）时的熔断页数——防病态续页死循环 */
const MAX_PAGES = 1000;

/** 纸盘明细三通道 URL 构造（fills/signals 即刻使用；equity 为 C4 预留） */
export function paperDetailUrl(
  channel: "fills" | "signals" | "equity",
  taskId: string,
  offset: number,
  limit: number = DETAIL_PAGE_SIZE,
): string {
  return `/api/paper-trading/tasks/${encodeURIComponent(taskId)}/${channel}`
    + `?limit=${limit}&offset=${offset}`;
}

/** 分页聚合：逐页拉取至页未满或 has_more=false，聚合保序返回全量 */
export async function pagedFetch<T>(options: {
  url: (offset: number, limit: number) => string;
  itemsKey: string;
  signal?: AbortSignal;
}): Promise<T[]> {
  const all: T[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const offset = page * DETAIL_PAGE_SIZE;
    const res = await fetch(
      options.url(offset, DETAIL_PAGE_SIZE), { signal: options.signal });
    if (!res.ok) {
      throw new Error(`纸盘明细端点请求失败（HTTP ${res.status}）`);
    }
    const body = (await res.json()) as Record<string, unknown>;
    const items = (body[options.itemsKey] ?? []) as T[];
    all.push(...items);
    if (body.has_more !== true || items.length < DETAIL_PAGE_SIZE) {
      return all;
    }
  }
  throw new Error(`纸盘明细分页异常：超过熔断页数（${MAX_PAGES}）`);
}
