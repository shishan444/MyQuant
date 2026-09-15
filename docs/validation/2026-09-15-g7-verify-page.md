# 验证报告 — G7 Verify 页迁移

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G7_

## 实施内容

- SSE 消费已接 G2 流式接口（G2.3 完成：useVerifyStream→createStreamHook→services→realtime/sse.ts），本批无改动 ✓
- 时间工具归位 lib：monthsAgo/todayISO（原 Verify/BatchBacktest 两页重复实现，下沉 lib/utils.ts；BatchBacktest 页在 G8 改接）
- 组件替换：ui/Button→Button（primary/text/danger 语义映射）、ui/input→Input+InputNumber（四数值参数）、ui/badge→Tag（来源/状态徽标）、自研进度条→Progress、Loader2→LoadingOutlined、ghost 图标按钮→Tooltip+aria-label（可访问性补全）
- 保留判定：两处迷你详情表（区间详情/历史结果）保留自研结构——纯展示无排序分页需求，AntD Table 无行为增益（等价线内最小改动）；日期输入保留原生 date input（YYYY-MM-DD 字符串形态与 localStorage 持久化/提交 payload 耦合，AntD DatePicker 需 dayjs 转换层，不引入）

## 测试（新建 3 用例，userEvent）

策略库空态引导/配置区（计数锚定主按钮+预设+日期输入）/历史视图切换往返

## 四件套

tsc 0 / build ✓ / vitest 30 文件 273 全绿（+3）/ lint 77 errors（基线内）/ knip 无新增（基线仅 G5 收缩项）
