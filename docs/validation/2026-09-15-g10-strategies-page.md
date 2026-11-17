# 验证报告 — G10 Strategies 页迁移

_时间：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G10_

## 实施内容

Strategies.tsx（1019 行）：

- **筛选/搜索 URL 化（状态所有权修正）**：searchQuery/sourceFilter/verifyFilter 从 useState 改 useSearchParams（?q=&source=&verify=，replace 更新不产生历史）——刷新可恢复（ui-state spec「筛选状态刷新可恢复」场景达成）
- **StrategyDetail 合并归位判定（实证）**：页内 ExpandPanel（策略信息+回测指标+操作）与 evolution/StrategyDetail（DNA 结构展示）职责不同重复度低——不合并；归位消除横向导入：StrategyDetail 迁至 components 根（共享层），lab/BacktestModePanel 与 evolution/TaskDetailDrawer 两消费方 import 更新（lab→evolution 横向消除）
- 组件替换：Button→AntButton 全量（含批量操作/行内 icon 按钮语义映射）、Input→Input+allowClear+prefix、Select→AntSelect（options 声明式+aria-label）、EditStrategyDialog（ConfirmDialog+散 useState）→Modal+AntD Form（草稿唯一持有者，useEffect 同步打开值）
- 既有测试改写：react-router mock 改 importOriginal+真实 useSearchParams（MemoryRouter 包裹——URL 化筛选用例走真实读写）；combobox 结构断言改 aria-label 锚定（断言语义保持：筛选器存在性）；badge 类断言不受影响（SourceBadge 保留自定义）

## 四件套

tsc 0 / build ✓ / vitest 31 文件 277 全绿（既有 18 用例改写后语义保持）/ lint 77 errors（基线内）/ knip 无新增
