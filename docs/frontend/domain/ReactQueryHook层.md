# React Query Hook 层 (Hooks)

## 职责与边界

**负责**：
- 将 services 层的 async 函数封装为 React Query hooks，管理缓存、轮询、失效策略
- 通过 query key factory 模式统一管理缓存键命名空间
- 封装 WebSocket 连接管理，将 WS 消息转化为 React Query 缓存更新（乐观更新 + 定时失效）
- 封装 useMutation，统一处理成功 toast / 失败 toast / 缓存失效
- 组合多个 query 为复合 hook（如 useChartIndicators 聚合 OHLCV + 指标数据）

**不负责**：
- HTTP 请求细节（由 services 层负责）
- 纯 UI 状态（由 stores 层负责）
- 组件渲染逻辑（由 pages / components 层负责）

**边界**：hooks 层是 React 组件与数据服务之间的桥梁。每个 hook 文件对应一个业务领域，函数粒度为单个 API 调用或单个 WebSocket 连接。所有 hooks 返回 React Query 的标准返回值（data / isLoading / error 等），组件通过解构使用。

## 接口与契约

### 对外暴露的接口

| 接口 | 类型 | 签名 | 说明 |
|------|------|------|------|
| `evolutionKeys` | Key Factory | `{all, tasks, task, history, discovered}` | 进化模块缓存键工厂 |
| `useEvolutionTasks` | Query Options | `(filters?) => queryOptions` | 进化任务列表，运行中 10s 轮询 |
| `useEvolutionTask` | Query Options | `(id) => queryOptions` | 单个任务详情，运行中 5s 轮询 |
| `useEvolutionHistory` | Query Options | `(id, isActive?) => queryOptions` | 代际历史，活跃时 3s 轮询 |
| `useDiscoveredStrategies` | Query Options | `(taskId?, minScore?) => queryOptions` | 发现策略列表 |
| `useCreateEvolutionTask` | Mutation | `() => useMutation` | 创建任务 + 失效任务列表缓存 |
| `useStopEvolutionTask` | Mutation | `() => useMutation` | 停止任务 + 失效详情和列表缓存 |
| `usePauseEvolutionTask` | Mutation | `() => useMutation` | 暂停任务 |
| `useResumeEvolutionTask` | Mutation | `() => useMutation` | 恢复任务 |
| `useEvolutionWebSocket` | Effect Hook | `(taskId) => void` | 订阅 WS 代际推送，处理 generation_complete / strategy_discovered / task_snapshot 等消息类型 |
| `strategiesKeys` | Key Factory | `{all, list, detail}` | 策略模块缓存键工厂 |
| `useStrategies` | Query Options | `(filters?) => queryOptions` | 策略列表查询 |
| `useStrategy` | Query Options | `(id) => queryOptions` | 策略详情查询 |
| `useRunBacktest` | Mutation | `() => useMutation` | 执行回测（失败 toast 持续显示） |
| `useCreateStrategy` | Mutation | `() => useMutation` | 创建策略 + 失效策略列表缓存 |
| `useDeleteStrategy` | Mutation | `() => useMutation` | 删除策略 |
| `useUpdateStrategy` | Mutation | `() => useMutation` | 更新策略元信息 |
| `tradingKeys` | Key Factory | `{all, tasks, task, trades, equity, metrics, runnerStatus}` | 交易模块缓存键工厂 |
| `useTradingTasks` | Query Hook | `() => useQuery` | 交易任务列表，运行中 5s 轮询 |
| `useTradingTask` | Query Hook | `(taskId) => useQuery` | 单任务详情，运行中 3s 轮询 |
| `useTradingTrades` | Query Hook | `(taskId, limit?) => useQuery` | 交易记录 |
| `useTradingEquity` | Query Hook | `(taskId) => useQuery` | 权益快照 |
| `useTradingMetrics` | Query Hook | `(taskId) => useQuery` | 交易指标 |
| `useRunnerStatus` | Query Hook | `() => useQuery` | Runner 状态 10s 轮询 |
| `useCreateTradingTask` | Mutation | `() => useMutation` | 创建交易任务 |
| `useStopTradingTask` | Mutation | `() => useMutation` | 停止交易任务 |
| `usePauseTradingTask` | Mutation | `() => useMutation` | 暂停交易任务 |
| `useResumeTradingTask` | Mutation | `() => useMutation` | 恢复交易任务 |
| `useRestartTradingTask` | Mutation | `() => useMutation` | 重新开始交易任务 |
| `useDeleteTradingTask` | Mutation | `() => useMutation` | 删除交易任务 |
| `useTradingWebSocket` | Effect Hook | `(taskId) => boolean` | 交易 WS 推送（position_update / task_started），返回连接状态 |
| `useVerifyScene` | Mutation | `() => useMutation` | 场景验证，无触发点时 toast 提示 |
| `datasetsKeys` | Key Factory | `{all, list, detail, ohlcv, availableSources}` | 数据集模块缓存键工厂 |
| `useDatasets` | Query Options | `(filters?) => queryOptions` | 数据集列表 |
| `useDataset` | Query Options | `(id) => queryOptions` | 数据集详情 |
| `useOhlcv` | Query Options | `(id, params?) => queryOptions` | OHLCV 数据 |
| `useImportCsv` | Mutation | `() => useMutation` | 单文件 CSV 导入 |
| `useImportCsvBatch` | Mutation | `() => useMutation` | 批量 CSV 导入 |
| `useDeleteDataset` | Mutation | `() => useMutation` | 删除数据集 |
| `useAvailableSources` | Query Options | `() => queryOptions` | 可用数据源（5min staleTime） |
| `useValidateHypothesis` | Mutation | `() => useMutation` | 假设验证 |
| `useValidateRules` | Mutation | `() => useMutation` | 规则验证 |
| `chartKeys` | Key Factory | `{ohlcv, indicators}` | 图表模块缓存键工厂 |
| `ohlcvOptions` | Query Options | `(symbol, tf, dateRange?, limit?) => queryOptions` | OHLCV 数据查询（60s staleTime） |
| `chartIndicatorOptions` | Query Options | `(symbol, tf, subChartType, params, opts?) => queryOptions` | 图表指标查询（60s staleTime） |
| `SubChartType` | Type | `"volume" \| "macd" \| "rsi" \| "kdj" \| "equity"` | 子图类型枚举 |
| `useChartIndicators` | Compound Hook | `(params) => UseChartIndicatorsResult` | 聚合 OHLCV + EMA/RSI/MACD/KDJ/布林带，从 chartSettings store 读取指标参数 |

### 对外暴露的数据结构

| 数据结构 | 类型 | 用途 |
|----------|------|------|
| `UseChartIndicatorsResult` | interface | 包含 candleData / chartIndicators / chartBollData / volumeData / macdData / kdjData 及 loading 状态 |
| `ChartIndicatorParams` | interface | 图表指标 API 请求参数 |

## 模块依赖

| 依赖模块 | 依赖原因 |
|----------|----------|
| services/ | 每个 hook 文件直接 import 对应 service 模块的函数作为 queryFn / mutationFn |
| @tanstack/react-query | queryOptions / useQuery / useMutation / useQueryClient 核心 API |
| sonner | 所有 useMutation 统一通过 toast 显示操作结果 |
| types/ | 使用 EvolutionTask、Strategy、DNA 等类型做类型守卫和缓存更新 |
| stores/chart-settings | useChartIndicators 从 Zustand store 读取 EMA/BOLL/RSI 配置决定查询参数 |
| lib/constants | useEvolution 引用 isActiveStatus 判断轮询策略 |

## 源码锚点

- [-> web/src/hooks/useEvolution.ts:1-308] 进化模块 hooks -- 9 个 hook + evolutionKeys factory + WebSocket 代际推送（处理 generation_complete / task_snapshot / phase_changed / strategy_discovered 等 6 种消息类型）
- [-> web/src/hooks/useStrategies.ts:1-78] 策略模块 hooks -- 7 个 hook + strategiesKeys factory，mutation 统一失效策略列表缓存
- [-> web/src/hooks/useTrading.ts:1-286] 交易模块 hooks -- 16 个 hook + tradingKeys factory + WS 推送，运行任务自动轮询（5s/3s），WS 断线 3s 重连
- [-> web/src/hooks/useScene.ts:1-20] 场景验证 hook -- 1 个 useMutation，处理零触发点提示
- [-> web/src/hooks/useDatasets.ts:1-84] 数据集 hooks -- 8 个 hook + datasetsKeys factory，availableSources 设置 5min staleTime
- [-> web/src/hooks/useValidation.ts:1-24] 验证 hooks -- 2 个 useMutation（假设验证 + 规则验证）
- [-> web/src/hooks/queries/chartQueries.ts:1-102] 图表查询配置 -- chartKeys factory + ohlcvOptions + chartIndicatorOptions（60s staleTime）
- [-> web/src/hooks/useChartIndicators.ts:1-191] 复合图表 hook -- 聚合 OHLCV + 指标查询，从 chartSettings store 读取参数，输出 KlineChart 所需的完整数据结构
