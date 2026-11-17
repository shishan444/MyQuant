# API 服务层 (Services)

## 职责与边界

**负责**：
- 封装所有后端 HTTP 调用，对外提供纯 async 函数接口
- 统一 Axios 实例配置（baseURL、超时、请求/响应拦截）
- 将后端响应格式转换为前端友好的数据结构
- 处理 422 校验错误的格式化解析（Pydantic validation errors）

**不负责**：
- 缓存策略、轮询、WebSocket（由 hooks 层负责）
- UI 反馈（toast 提示由 hooks 层处理）
- 请求状态的 loading/error 管理（由 React Query 管理）

**边界**：services 层是前端与后端 API 之间的唯一通道。每个文件对应一个后端领域模块。函数签名只依赖 types 中的类型定义，不依赖任何 UI 框架。

## 接口与契约

### 对外暴露的接口

| 接口 | 类型 | 签名 | 说明 |
|------|------|------|------|
| `api` | Axios 实例 | `api.get/post/put/delete` | 带拦截器的 Axios 实例，30s 超时，422 错误解析 |
| `getEvolutionTasks` | 查询 | `(params?) => Promise<EvolutionTaskListResponse>` | 分页查询进化任务列表 |
| `getEvolutionTask` | 查询 | `(id) => Promise<EvolutionTask>` | 获取单个进化任务详情 |
| `createEvolutionTask` | 命令 | `(payload) => Promise<EvolutionTask>` | 创建进化任务（auto/seed 模式） |
| `pauseEvolutionTask` | 命令 | `(id) => Promise<EvolutionTask>` | 暂停进化任务 |
| `resumeEvolutionTask` | 命令 | `(id) => Promise<EvolutionTask>` | 恢复进化任务 |
| `stopEvolutionTask` | 命令 | `(id) => Promise<EvolutionTask>` | 停止进化任务 |
| `getEvolutionHistory` | 查询 | `(id, params?) => Promise<EvolutionHistoryResponse>` | 获取代际历史记录（后端 generations 字段映射为 records） |
| `getDiscoveredStrategies` | 查询 | `(taskId, params?) => Promise<DiscoveredStrategy[]>` | 获取任务下发现的策略 |
| `getAllDiscoveredStrategies` | 查询 | `(params?) => Promise<DiscoveredStrategy[]>` | 获取全部发现策略 |
| `getStrategies` | 查询 | `(params?) => Promise<StrategyListResponse>` | 策略列表查询（支持 symbol/source/tags/sort） |
| `getStrategy` | 查询 | `(id) => Promise<Strategy>` | 获取策略详情 |
| `createStrategy` | 命令 | `(payload) => Promise<Strategy>` | 创建策略 |
| `updateStrategy` | 命令 | `(id, payload) => Promise<Strategy>` | 更新策略（name/tags/notes） |
| `deleteStrategy` | 命令 | `(id) => Promise<void>` | 删除策略 |
| `runBacktest` | 命令 | `(payload) => Promise<BacktestResult>` | 执行策略回测（60s 超时） |
| `compareStrategies` | 命令 | `(payload) => Promise<{results}>` | 策略对比回测 |
| `listTradingTasks` | 查询 | `(params?) => Promise<TradingTaskList>` | 查询交易任务列表 |
| `createTradingTask` | 命令 | `(params) => Promise<TradingTask>` | 创建模拟交易任务 |
| `stopTradingTask` | 命令 | `(taskId) => Promise<TradingTask>` | 停止交易任务 |
| `pauseTradingTask` | 命令 | `(taskId) => Promise<TradingTask>` | 暂停交易任务 |
| `resumeTradingTask` | 命令 | `(taskId) => Promise<TradingTask>` | 恢复交易任务 |
| `restartTradingTask` | 命令 | `(taskId) => Promise<TradingTask>` | 重新开始交易任务 |
| `deleteTradingTask` | 命令 | `(taskId) => Promise<{deleted}>` | 删除交易任务 |
| `getTradingTrades` | 查询 | `(taskId, limit?) => Promise<PaperTradeList>` | 获取交易记录 |
| `getTradingEquity` | 查询 | `(taskId) => Promise<EquitySnapshotList>` | 获取权益快照 |
| `getTradingMetrics` | 查询 | `(taskId) => Promise<TradingMetrics>` | 获取交易指标 |
| `getTradingRunnerStatus` | 查询 | `() => Promise<{is_alive, active_task_id}>` | 查询 Runner 状态 |
| `validateHypothesis` | 命令 | `(payload) => Promise<ValidateResponse>` | 假设验证 |
| `validateRules` | 命令 | `(payload) => Promise<RuleValidateResponse>` | 规则验证 |
| `getSceneTypes` | 查询 | `() => Promise<{types}>` | 获取场景类型列表 |
| `verifyScene` | 命令 | `(payload) => Promise<SceneVerifyResponse>` | 场景验证 |
| `getDatasets` | 查询 | `(params?) => Promise<DatasetListResponse>` | 数据集列表 |
| `importCsv` | 命令 | `(formData) => Promise<{dataset_id,...}>` | 单文件 CSV 导入（120s 超时） |
| `importCsvBatch` | 命令 | `(formData) => Promise<{...}>` | 批量 CSV 导入（300s 超时） |
| `deleteDataset` | 命令 | `(id) => Promise<void>` | 删除数据集 |
| `getOhlcv` | 查询 | `(id, params?) => Promise<OhlcvData>` | 按数据集 ID 获取 OHLCV |
| `getOhlcvBySymbol` | 查询 | `(symbol, tf, params?) => Promise<OhlcvData>` | 按交易对获取 OHLCV |
| `getChartIndicators` | 查询 | `(symbol, tf, params?) => Promise<ChartIndicatorsResponse>` | 获取图表技术指标数据 |

### 对外暴露的数据结构

| 数据结构 | 类型 | 用途 |
|----------|------|------|
| `TradingTask` / `TradingTaskList` | interface | trading.ts 中定义的交易任务和列表类型 |
| `PaperTrade` / `PaperTradeList` | interface | 交易记录和列表类型 |
| `CreateTradingTaskParams` | interface | 创建交易任务的请求参数 |
| `EquitySnapshot` / `EquitySnapshotList` | interface | 权益快照类型 |
| `TradingMetrics` | interface | 交易绩效指标 |
| `DiscoveryRule` / `DiscoveryResponse` | interface | 模式发现结果 |
| `SimilarCase` / `SimilarResponse` | interface | 相似案例结果 |
| `PredictResponse` | interface | 价格预测结果 |

## 模块依赖

| 依赖模块 | 依赖原因 |
|----------|----------|
| api.ts (axios) | 所有服务模块的基础 HTTP 客户端 |
| types/api | 复用核心领域类型（Strategy、DNA、EvolutionTask、Dataset 等） |
| types/scene | scene.ts 使用 SceneTypeInfo、SceneVerifyRequest、SceneVerifyResponse |

## 源码锚点

- [-> web/src/services/api.ts:1-45] Axios 实例工厂 -- baseURL 从环境变量读取，30s 超时，transformRequest 处理 FormData，422 错误拦截器解析 Pydantic 校验错误
- [-> web/src/services/evolution.ts:1-97] 进化服务 -- 7 个函数覆盖 CRUD + 控制（pause/resume/stop）+ 历史记录 + 发现策略查询
- [-> web/src/services/strategies.ts:1-79] 策略服务 -- 6 个函数覆盖 CRUD + 回测执行（60s 超时）+ 策略对比
- [-> web/src/services/trading.ts:1-173] 交易服务 -- 12 个函数覆盖全生命周期（create/stop/pause/resume/restart/delete）+ 数据查询（trades/equity/metrics），同时导出 7 个类型定义
- [-> web/src/services/validation.ts:1-17] 验证服务 -- 2 个函数：validateHypothesis（假设验证）和 validateRules（规则验证）
- [-> web/src/services/scene.ts:1-20] 场景服务 -- 2 个函数：getSceneTypes（获取类型列表）和 verifyScene（场景验证）
- [-> web/src/services/datasets.ts:1-106] 数据集服务 -- 8 个函数覆盖数据集 CRUD + CSV 导入（单文件 120s / 批量 300s）+ OHLCV 数据 + 图表指标
