# G14 图表与动效迁移 Evidence

日期：2026-09-15 | 分支：fix/myquant-truthfulness-batch1 | tag：FE-G14

## 14.1 recharts→echarts 按需引入迁移

### 迁移面（生产 2 文件 + 唯一化收敛）

| 文件 | 动作 |
|---|---|
| `components/charts/core/echartsCore.ts` | 新增：echarts 按需注册单点（LineChart/ScatterChart/Grid/Tooltip/MarkLine/Canvas）+ ComposeOption 类型 |
| `components/charts/core/chartPalette.ts` | 新增：图表色集中常量（G4 冻结「图表色不入 token」；语义色对齐 antdThemeConfig：profit #00C853 / loss #EF4444 / gold #F59E0B）+ withAlpha 工具 |
| `components/charts/core/equityOption.ts` | 新增：buildEquityOption / resolveEquityColor / EQUITY_MIN_POINTS（option 纯函数，L1 直测入口） |
| `components/charts/core/scoreTrendOption.ts` | 新增：buildScoreTrendOption + renderPointTooltip（CustomTooltip 全字段迁移为 formatter 回调：delta/达标/累计最佳/平均/交易/多样性/停滞/新种群/最优刷新） |
| `components/charts/EquityCurve.tsx` | 新增：共享权益曲线组件（薄壳：init/resize/dispose + setOption 单一持有） |
| `components/evolution/ScoreTrendChart.tsx` | 重写：recharts ComposedChart→echarts；props 接口不变；legend/stats JSX 原样保留 |
| `components/trading/EquityCurve.tsx` | 删除（recharts 版，并入共享组件） |
| `components/lab/EquityCurveChart.tsx` | 删除（lightweight-charts 版，并入共享组件） |
| `components/evolution/BacktestDrawer.tsx` | 删内联 lightweight-charts EquityCurve（约 60 行重复实现），接共享组件 |

### 权益曲线/K线共享组件唯一化（design.md G14 冻结项）

迁移前 4 个独立权益曲线实现 → 迁移后 1 个：

| 实现 | 消费方 | 迁移后 |
|---|---|---|
| trading/EquityCurve（recharts，盈亏着色） | Trading | 共享组件 + baseline 参数 |
| lab/EquityCurveChart（lightweight-charts，金线） | BacktestModePanel、BatchBacktest | 共享组件（无 baseline=金线语义保持） |
| BacktestDrawer 内联（lightweight-charts 复制品） | Evolution 回测抽屉 | 共享组件 |
| KlineChart subChartType="equity" 子图 | BacktestModePanel/SceneModePanel K 线内嵌 | **保留**（K 线形态一部分，与独立曲线图语义不同） |

### 消费点迁移（5 处）

- `pages/Trading.tsx`：`{timestamp, equity}` → `{timestamp, value}` 映射 + initialCash→baseline
- `components/lab/BacktestModePanel.tsx`：EquityCurveChart→共享 EquityCurve（height=180 保持）
- `pages/BatchBacktest.tsx`：import 改直连（lab/index.ts 移除 EquityCurveChart 导出）
- `components/evolution/BacktestDrawer.tsx`：内联删除 + import 共享版
- `components/charts/core/time.ts`：过时注释修正

### 测试

- 新增 `test/components/charts/EquityCurve.test.tsx`（15 用例）：L1 resolveEquityColor 四态（无基准金/盈利绿/亏损红/持平绿）+ buildEquityOption 七项（数据映射/类目轴/盈亏着色/渐变两档/K-M 缩写/时间格式/tooltip）；L2 mock echarts/core 四项（空态文案+init 未调/init+setOption 参数/baseline 变化 notMerge/dispose）
- 新增 `test/components/evolution/ScoreTrendChart.test.tsx`（14 用例）：L1 八项（系列布局/目标线回退/达标线 markLine/种群边界竖线/y 轴域计算/champion scatter/tooltip 富信息三态/越界回退）；L2 四项（空态/init+legend+stats/目标线 legend 分支/notMerge rerender/dispose）
- 删除旧 `test/components/trading/EquityCurve.test.tsx`（断言仅 mock 调用计数，语义已迁入新 L1/L2 且更强）
- `test/fixtures.ts` 删 mockEquitySnapshots（旧契约 {equity,balance} 随旧组件退役，唯一消费者为已删测试）
- `BacktestModePanel.test.tsx` mock 路径更新（lab/EquityCurveChart→charts/EquityCurve）
- 空态文案统一中文「数据不足，无法绘制资金曲线」（原文案 "No equity data yet" 违反文案铁律，G17 终检项提前消除）

### 验证

- recharts 依赖移除（package.json + pnpm-lock，全库 grep 零残留）
- 四件套：tsc -b 0 错误 / vitest 32 文件 300 全绿（277→300）/ lint 86 problems（73e+13w，与 HEAD 基线逐数持平零新增）/ build ✓
- knip 唯一名 diff 零新增（顺手消除 G1 基线配置项：ignoreDependencies 的 user-event 已被 knip 判定可直接移除）

## 14.2 framer-motion→motion/react + 动效降级

### 迁移面

- `components/PageTransition.tsx`：import 换 motion/react + **useReducedMotion 降级**（偏好时渲染普通 div 直接呈现终态，跳过 opacity/y 进入动画）
- `pages/Strategies.tsx`、`pages/BatchBacktest.tsx`：import 路径替换（motion/AnimatePresence 同名 API，零逻辑改动）
- 4 个测试文件 mock 目标 framer-motion→motion/react + 工厂补 useReducedMotion 导出
- framer-motion 依赖移除，motion@13.3.0 引入

### 测试

新增 `test/components/PageTransition.test.tsx`（2 用例，motion/react 部分mock：真实 motion 组件 + 覆写 useReducedMotion）：
- 无偏好：motion.div 应用 initial 样式（opacity:0 起点）
- 偏好降级：普通 div 直接呈现，无初始透明样式

### 真实浏览器冒烟（前后端真实服务 + Playwright）

| 检查项 | 结果 |
|---|---|
| Evolution 任务详情「进化曲线」tab | ScoreTrendChart canvas 1040×400 **中心像素 painted=true**（真实绘制）；legend 5 项（综合得分/适应度/累计最佳/平均得分/最优更新）+ stats（最优刷新/最长停滞）齐全 |
| Lab 策略回测（近3月区间）| 共享 EquityCurve canvas 1576×184 painted=true；「资金曲线」「总收益」渲染；K 线主图（lightweight-charts 1560×600）正常 |
| 五页导航（evolution/strategies/lab/batch-backtest/trading）| console **零 error 零 warning** |
| 动效降级 | matchMedia 覆写 prefers-reduced-motion:reduce + SPA 导航 /settings：PageTransition 渲染普通 div（opacity:1/transform:none，无动画起始态） |

## 服务冒烟环境

- 后端：venv python scripts/api_run.py（8000，health ok，version 0.16.0）
- 前端：vite dev 5173；冒烟后服务已停（端口 0 监听）
