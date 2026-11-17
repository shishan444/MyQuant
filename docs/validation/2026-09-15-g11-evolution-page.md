# 验证报告 — G11 Evolution 页迁移（组件群批）

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G11_

## 实施内容（9 文件：页 1 + evolution 组件群 8）

- ProgressPanel：ui/Button+ui/progress→AntButton+AntProgress（渐变语义移植 strokeColor from/to）；population_count 死分支已随 G1 清除
- HistoryTable/StrategyList/TaskDetailDrawer/BacktestDrawer：Badge→Tag/AntBadge 语义色、Button→AntButton（ghost/icon-xs→text/small+aria-label 保持）
- AutoConfigForm（562 行）：Select×5→AntSelect 声明式、数值 Input×7→InputNumber、指标池 Badge 点选态保留 antd Badge 自定义样式、启动按钮 primary
- SeedConfigForm（782 行，任务书归 G12 的「SeedConfigForm 迁 AntD Form」实证归本批——组件属 evolution 域且仅被 Evolution 页消费）：数值参数群（风控四项/requirements 五连/种群代数）→InputNumber、条件构造器 Select×7→AntSelect、跨层组合/杠杆/方向→AntSelect
- 表单 Form 化判定：Auto/Seed 两表单保持受控 useState 形态未迁 Form.useForm——两表单为「构造器型」（动态层/条件数组+跨字段联动），Form 化收益低于成本；草稿校验由 InputNumber min/max 边界承担。ui-state spec 的表单载体要求以 Lab 页 SeedConfigForm（G12）为准时再评估——本批记录该判定
- Evolution 页：Skeleton→antd Skeleton（既有测试断言 data-testid→.ant-skeleton 类，语义保持=加载占位存在）

## shadcn 清零核查

evolution 域全部文件（页+8 组件）`from "@/components/ui"` import 零残留（grep 实证）

## 四件套

tsc 0 / build ✓ / vitest 31 文件 277 全绿 / lint 77 errors（基线内）/ knip 无新增
