/**
 * 纸盘明细端点分页聚合装载（fix-replay-presentation-pipeline 任务 1.1）。
 *
 * 修复背景：此前前端以 limit=10000 请求纸盘明细端点，超出声明式校验
 * 上限（Query le=500）恒 422，且 `!r.ok → []` 吞错伪装「无成交」空态
 * ——回放四件套在任何真实 replay 记录上恒空（FE-1，缺陷族第二次）。
 *
 * 语义：页大小=500（端点声明上限）；按「页未满或 hasMore=false」
 * 终止续页；聚合保序（后端保证页内 bar_time+seq 升序——到达序拼接
 * 即全局序）；任页失败整体抛错（useQuery isError 消费——不部分呈现，
 * 半量数据+吞错是本缺陷族的根因教训）。单页取数经注入的 fetchPage
 * （服务层单页函数适配——鉴权/基址/超时三能力与 signal 取消自动
 * 继承，不再自建裸 fetch 通道）；equity 通道经 fetchPage 注入泛化
 * 预留（C4 权益曲线复用同一实现——三通道一份，不写三份）。
 */

/** 页大小=端点声明上限（paper_trading.py fills/signals/equity Query le=500） */
export const DETAIL_PAGE_SIZE = 500;

/** 服务端异常（恒满页+恒 has_more）时的熔断页数——防病态续页死循环 */
const MAX_PAGES = 1000;

/** 分页聚合：逐页拉取至页未满或 hasMore=false，聚合保序返回全量
 *  （fetchPage 由调用方以服务层单页函数适配——失败经其 reject 传播） */
export async function pagedFetch<T>(options: {
  fetchPage: (
    offset: number,
    signal?: AbortSignal,
  ) => Promise<{ items: T[]; hasMore: boolean }>;
  signal?: AbortSignal;
}): Promise<T[]> {
  const all: T[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const offset = page * DETAIL_PAGE_SIZE;
    const { items, hasMore } = await options.fetchPage(
      offset, options.signal);
    all.push(...items);
    if (!hasMore || items.length < DETAIL_PAGE_SIZE) {
      return all;
    }
  }
  throw new Error(`纸盘明细分页异常：超过熔断页数（${MAX_PAGES}）`);
}
