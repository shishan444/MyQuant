# API 接口层

## 职责与边界

**负责**：
- HTTP API 接口暴露（FastAPI 框架）
- WebSocket 实时推送（进化进度 / 策略发现）
- 后台任务执行（EvolutionRunner 守护线程）
- 数据库初始化与扩展操作
- 依赖注入与配置管理
- Pydantic V2 请求/响应模型定义
- CORS 中间件配置

**不负责**：
- 业务逻辑实现（委托给 core 层模块）
- 数据持久化细节（委托给 core/persistence）

**边界**：本模块是系统与外部（前端 / CLI）的唯一交互入口。所有 HTTP/WebSocket 请求通过路由模块分发到 core 层处理。EvolutionRunner 作为后台守护线程，从 SQLite 拉取待执行任务并驱动进化循环。

## 接口与契约

### 对外暴露的接口

| 路由模块 | 前缀 | 核心端点 | 说明 |
|---|---|---|---|
| config | /api/config | GET / | 获取系统配置（时间周期 / 交易对 / 模板） |
| data | /api/data | GET /status, POST /update | 数据状态查询 + 手动触发数据更新 |
| evolution | /api/evolution | POST /start, POST /stop, GET /status | 进化任务创建 / 停止 / 状态查询 |
| strategies | /api/strategies | GET /, GET /{id} | 策略列表 + 策略详情 |
| validate | /api/validate | POST / | DNA 校验接口 |
| ws | /ws | WebSocket /ws/{task_id} | 实时进化进度推送 |
| trading | /api/trading | POST /start, POST /stop | 实盘/模拟交易任务管理 |
| chart_config | /api/chart-config | GET / | 图表配置接口 |
| scene | /api/scene | GET /, POST / | 场景管理（多策略对比场景） |

### 核心内部组件

| 组件 | 类型 | 说明 |
|---|---|---|
| create_app | 函数 | FastAPI 应用工厂：创建实例 + 配置 CORS + 挂载路由 + 管理生命周期 |
| EvolutionRunner | Thread | 守护线程：轮询 SQLite 待执行任务，驱动 EvolutionEngine，通过 WebSocket 推送进度 |
| TaskController | class | 线程安全任务控制器：通过 threading.Event 实现协作式取消 |
| ChampionTracker | class | 冠军追踪器：线程安全的原子 score + metrics 更新 |
| set_ws_push_fn | 函数 | 注册 WebSocket 推送函数（从 FastAPI 主线程注入到 Runner 线程） |

### 生命周期管理

```
应用启动:
  1. init_db_ext(db_path)         -- 初始化数据库扩展
  2. recover_stale_tasks(db_path) -- 崩溃恢复：将 running 状态标记为 stopped
  3. 创建 EvolutionRunner + TradingRunner
  4. set_ws_push_fn() 注册跨线程 WS 推送
  5. runner.start()               -- 启动守护线程

应用关闭:
  1. set_ws_push_fn(None)         -- 断开 WS 推送
  2. runner.stop() + join()       -- 停止守护线程
```

## 业务规则与不变量

1. **任务状态机**：pending -> running -> completed / stopped（通过 SQLite 状态字段管理）
2. **崩溃恢复**：启动时 recover_stale_tasks 将所有 running 状态重置为 stopped，防止任务假死
3. **心跳检测**：运行中任务定期更新 heartbeat_at，超时 5 分钟未更新则标记为 stopped
4. **协作式取消**：TaskController 使用 threading.Event，在进化循环的关键点（evaluate_fn / on_generation）检查停止请求
5. **跨线程 WS 推送**：Runner 线程通过 asyncio.run_coroutine_threadsafe 将消息推送到 FastAPI 事件循环
6. **连续进化**：任务完成后自动启动新种群（continuous=1），注入前代冠军 + 随机模板种子，跨种群累积代数偏移
7. **策略自动提取**：进化过程中分数超过 strategy_threshold（默认 80）的策略自动保存到策略表
8. **种群级批评估**：优先使用 BacktestEngine.batch_run 进行种群级评估，失败时回退到逐个评估

## 设计意图

API 层采用"应用工厂"模式（create_app），使得测试和配置注入更加灵活。EvolutionRunner 作为独立守护线程运行，与 FastAPI 主线程通过 threading.Event 和 asyncio.run_coroutine_threadsafe 进行跨线程通信。这种设计避免了阻塞 FastAPI 事件循环，同时保持了实时推送能力。

任务控制器（TaskController）采用协作式取消而非强制中断：进化引擎在每代结束后检查停止标志，保证了数据一致性。心跳机制作为额外的安全保障，检测并处理因进程崩溃导致的任务假死。

## 模块依赖

| 依赖模块 | 依赖原因 |
|---|---|
| evolution/engine | EvolutionEngine（进化执行） |
| evolution/diversity | compute_diversity / compute_phenotype_diversity（多样性指标） |
| evolution/champion | ChampionTracker（冠军追踪） |
| strategy/dna | StrategyDNA（DNA 解析 / 序列化） |
| backtest/engine | BacktestEngine（回测评估） |
| strategy/executor | dna_to_signal_set / batch_signal_sets（信号计算） |
| scoring/scorer | score_strategy（评分计算） |
| scoring/templates | get_template（模板获取） |
| data/mtf_loader | load_and_prepare_df / load_mtf_data（数据加载） |
| persistence/db | SQLite 持久化操作 |
| trading/runner | TradingRunner（模拟交易执行器） |
| trading/pipeline | DecisionPipeline（决策管线） |
| trading/replay | ReplayRunner（历史回放） |
| prediction/predictor | PriceRangePredictor（价格区间预测） |
| prediction/genes | PredictionDNA（预测公式参数） |

## 源码锚点

- [-> api/app.py:26-148] create_app：应用工厂 + 生命周期管理 + 路由挂载
- [-> api/runner.py:47-69] TaskController：协作式取消控制器
- [-> api/runner.py:125-157] recover_stale_tasks / check_stale_heartbeats：崩溃恢复与心跳检测
- [-> api/runner.py:177-903] EvolutionRunner：守护线程主循环 + 任务执行 + 评估函数 + 连续进化
- [-> api/db_ext.py] init_db_ext / save_strategy：数据库扩展操作
- [-> api/deps.py] 依赖注入配置
- [-> api/schemas.py] Pydantic V2 请求/响应模型
- [-> api/routes/] 9 个路由模块：config / data / evolution / strategies / ws / trading / validate / chart_config / scene
