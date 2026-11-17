# UI 组件库 (Components)

## 职责与边界

**负责**：
- 提供可复用的 UI 组件，按业务领域分组（layout / charts / evolution / lab / trading / ui / 通用）
- 每个组件保持高内聚，接收 props 驱动渲染，通过回调函数上报用户操作
- 复杂组件（KlineChart、ScoreTrendChart 等）封装底层渲染库，对外暴露声明式 API

**不负责**：
- 数据获取和缓存（由 hooks 层负责）
- 全局状态管理（由 stores 层负责）
- 路由和页面级编排（由 pages 层负责）

**边界**：components 层是纯展示层。组件通过 props 接收数据和回调，不直接调用 services 或管理 React Query 缓存。例外是 useChartSettings 等 store 的读取（用于获取指标颜色/配置）。

## 接口与契约

### 对外暴露的接口

#### layout/ -- 全局布局

| 接口 | 说明 |
|------|------|
| `AppLayout` | 全局布局骨架 -- 侧边栏 + 内容区 + 顶部标题栏 |
| `Sidebar` | 侧边栏导航 -- 路由菜单项 + 标题栏 + 折叠控制 |

#### charts/ -- 图表组件

| 接口 | 说明 |
|------|------|
| `KlineChart` | 轻量级 K 线图组件（基于 lightweight-charts），支持 OHLCV + EMA/布林带叠加 + 成交量子图 + 注解层 + ref 暴露 imperative API |
| `ChartToolbar` | 图表工具栏 -- 周期切换/缩放/指标开关 |
| `ChartLegend` | 图表图例 -- 指标名称/颜色/值 |
| `AnnotationLayer` | 注解层 -- 在 K 线图上标记买卖信号 |
| `ChartEmbeddedLegend` | 内嵌图例 -- 绘制在图表内部 |
| `core/chartThemes` | 图表主题配置 -- 深色/浅色主题的网格/蜡烛/线条颜色 |
| `core/useChartSync` | 图表同步 hook -- 多图表时间轴联动 |

#### evolution/ -- 进化领域组件

| 接口 | 说明 |
|------|------|
| `AlgorithmLog` | 算法日志面板 -- 展示变异操作记录 |
| `AutoConfigForm` | 自动探索配置表单 -- symbol/周期池/指标池/种群/代数/目标分数/杠杆/方向 |
| `BacktestDrawer` | 回测抽屉 -- 侧边弹出展示策略回测结果 |
| `HistoryTable` | 历史任务表格 -- 已完成/已停止任务列表 |
| `ProgressPanel` | 进度面板 -- 当前进度条 + 控制（暂停/恢复/停止） |
| `QuickPresets` | 快速预设 -- 常用探索配置一键选择 |
| `ScoreTrendChart` | 得分趋势图 -- 代际最佳/平均分折线 + 目标线 + 冠军变更标记 |
| `SeedConfigForm` | 种子配置表单 -- 初始 DNA + 参数 |
| `SegmentedControl` | 分段控制器 -- auto/seed 模式切换 |
| `StrategyDetail` | 策略详情 -- DNA 结构/指标参数/风险参数展示 |
| `StrategyList` | 策略列表 -- 发现策略卡片列表 + 展开详情 |
| `TaskDetailDrawer` | 任务详情抽屉 -- 侧边弹出展示完整任务信息 |

#### lab/ -- 实验室领域组件

| 接口 | 说明 |
|------|------|
| `ActionDropdown` | 操作下拉 -- 条件类型选择（lt/gt/cross_above 等） |
| `BacktestMetricsPanel` | 回测指标面板 -- 年化收益/夏普/回撤/胜率等 |
| `BacktestModePanel` | 回测模式面板 -- 选择数据集 + 时间范围 + 运行回测 |
| `ConditionPill` | 条件标签 -- 单个 WHEN/THEN 条件的胶囊展示 |
| `ConditionPillGroup` | 条件标签组 -- 多个条件的组合展示 |
| `DistributionChart` | 分布图 -- 验证结果的收益分布直方图 |
| `DropdownPortal` | 下拉传送门 -- 解决溢出容器中的下拉定位 |
| `EquityCurveChart` | 权益曲线图 -- 回测权益变化折线 |
| `ReferencePanel` | 参考面板 -- 常用指标条件参考 |
| `RuleConditionGroup` | 规则条件组 -- WHEN/THEN 条件编辑器 |
| `RuleConditionRow` | 规则条件行 -- 单条条件编辑（subject/action/target/logic/timeframe） |
| `SaveStrategyDialog` | 保存策略对话框 -- 输入名称/标签保存当前策略 |
| `SceneModePanel` | 场景模式面板 -- 场景类型选择 + 参数配置 + 运行验证 |
| `SceneResult` | 场景验证结果 -- 触发点列表 + 各时间跨度统计 |
| `SceneSelector` | 场景选择器 -- 场景类型下拉 |
| `SubjectDropdown` | 指标主体下拉 -- 选择 RSI/EMA/MACD 等指标 |
| `TargetInput` | 目标值输入 -- 条件阈值输入框 |
| `TimeframeLabel` | 周期标签 -- 周期展示组件 |
| `TimeframeSelector` | 周期选择器 -- 单周期或多周期池选择 |
| `TriggerDetailDrawer` | 触发详情抽屉 -- 单个触发点的详细信息 |
| `TriggerTable` | 触发表格 -- 验证触发点列表 |
| `ValidationConclusion` | 验证结论 -- 综合评估结论展示 |

#### trading/ -- 交易领域组件

| 接口 | 说明 |
|------|------|
| `CreateTaskDialog` | 创建任务对话框 -- DNA 输入/交易对/周期/杠杆/方向/初始资金 |
| `EquityCurve` | 权益曲线 -- 模拟交易权益变化折线 |
| `MetricsDashboard` | 指标仪表板 -- 总收益/胜率/盈亏比/最大回撤等 |
| `RunnerStatusBadge` | Runner 状态徽标 -- 后台 Runner 存活状态指示 |
| `TradingChart` | 交易图表 -- K 线图 + 买卖信号标记 |

#### 通用组件

| 接口 | 说明 |
|------|------|
| `ConfirmDialog` | 确认对话框 -- 统一的确认/取消交互（支持 destructive 变体） |
| `EmptyState` | 空状态 -- 图标 + 标题 + 描述 + 操作按钮 |
| `ErrorBoundary` | 错误边界 -- 捕获子组件渲染异常 |
| `GlassCard` | 玻璃卡片 -- 毛玻璃效果容器（hover 动画可配置） |
| `PageTransition` | 页面过渡 -- framer-motion 淡入动画 |
| `StatCard` | 统计卡片 -- 数值 + 标签 + 趋势指示 |

#### ui/ -- shadcn/ui 基础组件

Button、alert-dialog、badge、card、dialog、dropdown-menu、form、input、label、progress、scroll-area、select、separator、skeleton、slider、switch、table、tabs、tooltip

## 模块依赖

| 依赖模块 | 依赖原因 |
|----------|----------|
| types/ | 组件 props 使用 Strategy、EvolutionTask、DNA、BacktestResult、PaperTrade 等类型 |
| stores/ | KlineChart/useChartIndicators 读取 useChartSettings 指标配置 |
| lib/ | cn() 样式合并、formatCurrency/formatPercent 格式化、constants 选项常量 |
| hooks/ | 图表组件使用 useChartIndicators 获取数据；部分组件通过 props 接收 hooks 返回值 |

## 源码锚点

- [-> web/src/components/layout/AppLayout.tsx] 全局布局骨架
- [-> web/src/components/layout/Sidebar.tsx] 侧边栏导航
- [-> web/src/components/charts/KlineChart.tsx] 轻量级 K 线图（lightweight-charts 封装）
- [-> web/src/components/charts/core/chartThemes.ts] 图表主题配置
- [-> web/src/components/charts/core/useChartSync.ts] 多图表时间轴同步
- [-> web/src/components/evolution/ScoreTrendChart.tsx] 进化得分趋势图
- [-> web/src/components/evolution/ProgressPanel.tsx] 进化进度面板
- [-> web/src/components/evolution/AutoConfigForm.tsx] 自动探索配置表单
- [-> web/src/components/evolution/SeedConfigForm.tsx] 种子配置表单
- [-> web/src/components/evolution/StrategyList.tsx] 发现策略列表
- [-> web/src/components/lab/RuleConditionGroup.tsx] WHEN/THEN 规则条件编辑器
- [-> web/src/components/lab/BacktestModePanel.tsx] 回测模式面板
- [-> web/src/components/lab/SceneModePanel.tsx] 场景模式面板
- [-> web/src/components/trading/CreateTaskDialog.tsx] 创建交易任务对话框
- [-> web/src/components/trading/MetricsDashboard.tsx] 交易指标仪表板
- [-> web/src/components/trading/TradingChart.tsx] 交易 K 线图 + 信号标记
