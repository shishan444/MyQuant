# 验证报告 — G8 BatchBacktest 页迁移

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G8_

## 实施内容

- SSE 消费已接 G2 流式接口（useBatchBacktestStream→createStreamHook→realtime/sse.ts），本批零改动 ✓
- 回测面板合并核查：详情展开消费 @/components/lab 的 EquityCurveChart+BacktestMetricsPanel——lab 域唯一实现无平行副本，「合并」已达成（记录，无动作）
- 时间工具改接 lib（G7 归位的 monthsAgo/todayISO，页内副本删除）
- 组件替换：Button→Button（筛选/预设/详情/取消/重试全套语义映射）、Input→Input+InputNumber（InlineParam 数值化+aria-label）、自研进度条→Progress、Loader2→LoadingOutlined、删除区间按钮补 aria-label
- 保留判定：策略选择表/周期详情表保留自研结构（行点击选择/嵌套展开等强定制交互，AntD Table 改造成本大于收益，等价线内保留）；framer-motion 保留（G14 批统一迁 motion/react）

## 测试（新建 4 用例，userEvent）

策略选择联动计数/搜索过滤空态/空策略库引导/**LoadDetail 断言语义保持**（展开策略行→点详情→fetchBatchBacktestDetail("r1") 调用+资金曲线呈现）

## 四件套

tsc 0 / build ✓ / vitest 31 文件 277 全绿（+4）/ lint 77 errors（基线内）/ knip 无新增
