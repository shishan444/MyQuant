# MyQuant AI Coding Guide

本文件用于指导 AI coding。目标不是替代完整工程文档，而是让 AI 在每次动手前快速判断任务边界、应读上下文、禁止方向和验证方式，减少无效工作。

## 1. 项目定义

MyQuant 是一个本地运行的 BTC/ETH 加密货币量化策略进化工具。

它支持：

- 策略 DNA 构建与校验
- 技术指标与信号执行
- 策略回测与评分
- 遗传进化搜索
- 假设验证与模式发现
- 价格区间预测
- 模拟纸盘交易
- 前端可视化与任务管理

它不做：

- 真实账户管理
- 真实资金下单
- 实盘交易执行
- 生产部署系统
- 非加密货币品种支持

任何涉及真实交易、凭证、账户状态、生产部署的任务，必须先向用户确认。

## 2. 开始任务前的判断

每次任务开始前，先判断目标属于哪个分区：

| 任务类型 | 主要路径 | 说明 |
|---|---|---|
| 后端 API | `api/` | FastAPI 路由、Pydantic schema、后台 runner、WebSocket |
| 核心计算 | `core/` | 策略、回测、进化、评分、验证、预测、交易管线 |
| 数据与存储 | `core/data/`, `core/persistence/`, `migrations/` | K 线、Parquet、SQLite、任务和策略持久化 |
| 前端 Web | `web/src/` | React 页面、组件、hooks、services、stores、types |
| 文档设计 | `docs/` | 架构、领域、流程、横切规则 |
| 草稿材料 | `works/` | 设计稿、草稿、截图、规划材料 |

除非用户明确指定，不要把 `works/` 内容当作生产源码依据。

## 3. 文档入口

先读总入口：

- `docs/MAP.md`
- `docs/backend/INDEX.yaml`
- `docs/frontend/INDEX.yaml`

然后按任务选择具体文档：

- 后端领域：`docs/backend/domain/*.md`
- 后端流程：`docs/backend/flows/*.md`
- 后端横切规则：`docs/backend/crosscut/*.md`
- 前端领域：`docs/frontend/domain/*.md`
- 前端流程：`docs/frontend/flows/*.md`
- 前端横切规则：`docs/frontend/crosscut/*.md`

不要只凭文件名猜模块行为。修改代码前必须读取对应源码、邻近测试和相关文档。

## 4. 架构边界

### 后端

后端是分层单体：

```text
api/ -> core/ -> data/persistence/infrastructure
```

API 层负责请求入口、schema、路由、依赖注入、后台 runner 和 WebSocket 推送。核心业务逻辑应委托给 `core/`。

`core/` 按领域拆分：

- `core/strategy/`：策略 DNA、条件评估、信号生成
- `core/backtest/`：vectorbt 回测、止损止盈、杠杆、清算、资金费率
- `core/features/`：指标注册和计算
- `core/scoring/`：评分模板和指标标准化
- `core/evolution/`：遗传进化、变异交叉、多样性、早停
- `core/validation/`：假设验证、规则验证、场景验证
- `core/discovery/`：模式发现、相似案例、规则提取
- `core/prediction/`：价格区间预测，保持自包含
- `core/trading/`：模拟纸盘交易、决策管线、回放、runner
- `core/persistence/`：SQLite 持久化

### 前端

前端是页面、hook、service 分层：

```text
pages -> hooks -> services -> backend API
pages -> stores/components/lib/types
```

基本边界：

- `services/` 封装 HTTP 调用
- `hooks/` 管 React Query、缓存、轮询、mutation、WebSocket
- `pages/` 组合页面和页面级状态
- `stores/` 管 UI 或跨页面客户端状态
- `components/` 管可复用 UI 与业务组件
- `types/` 管 API 契约和领域类型

页面层不要直接复制 service/hook 中已有的数据请求逻辑。

## 5. 关键不变量

### 交易与回测

- 回测和交易逻辑必须避免未来函数和数据泄漏。
- 信号进入回测时必须考虑 `shift(1)`。
- 止损止盈基于 K 线 HIGH/LOW 判断，不应只用 close 简化。
- 手续费、滑点、资金费率、杠杆、清算逻辑不能随意删除或绕过。
- 修改回测、评分、交易执行逻辑时，必须做最小回归验证。

### 策略 DNA

- `StrategyDNA` 是核心数据结构。
- strategy 模块负责把 DNA 翻译成信号，不负责 I/O、回测、评分或进化。
- 新增条件、角色或字段时，必须同步考虑：
  - Python 数据结构
  - 序列化/反序列化
  - 校验逻辑
  - 执行逻辑
  - API schema
  - 前端类型
  - 测试

### 预测模块

- `core/prediction/` 保持自包含。
- 不直接 import `core/` 下其他模块。
- 与特征工程通过 DataFrame 列名约定松耦合。

### API 与后台任务

- API 层不复制核心业务逻辑。
- 后台任务通过协作式取消，不强制杀线程。
- runner 与 FastAPI 事件循环之间的 WebSocket 推送要考虑跨线程安全。
- 任务状态机、心跳、崩溃恢复逻辑不能随意简化。

### 前端

- 用户可见流程必须处理 loading、error、empty、disabled 状态。
- API 错误尤其是 422 校验错误，应保持现有格式化处理。
- 表格、按钮、标签、图表和面板在常见桌面与移动宽度下不能重叠或溢出。
- 操作型页面应保持信息密度和可扫描性，不做营销式 hero 页面。

## 6. 按任务类型读取清单

### 修改策略 DNA 或信号执行

必须读：

- `docs/backend/domain/策略DNA与执行.md`
- `core/strategy/dna.py`
- `core/strategy/executor.py`
- `core/strategy/validator.py`
- strategy 相关测试

注意：

- 不要在 strategy 模块加入 I/O。
- 不要把回测逻辑写入 strategy。
- 不要只改后端，忘记 API schema 和前端类型。

### 修改回测

必须读：

- `docs/backend/domain/回测引擎.md`
- `core/backtest/engine.py`
- `docs/backend/crosscut/配置管理.md`
- backtest/scoring 相关测试

注意：

- 单策略回测和批量回测都要考虑。
- 不要为了测试通过删除费用、杠杆、清算或 shift 行为。
- 结果指标变化应说明原因。

### 修改进化

必须读：

- `docs/backend/domain/进化引擎.md`
- `docs/backend/flows/策略进化流程.md`
- `core/evolution/`
- `api/runner.py`
- evolution 相关测试

注意：

- 进化任务受 API runner、SQLite 状态、WebSocket 推送共同影响。
- 修改评估函数时要考虑批量回测 fallback。
- 不要破坏连续进化、冠军追踪、早停和多样性维护。

### 修改 API

必须读：

- `docs/backend/domain/API接口层.md`
- `api/app.py`
- `api/routes/`
- `api/schemas.py`
- 对应 core 模块文档

注意：

- API 层只做入口和编排。
- 请求/响应模型使用 Pydantic V2。
- 改接口时同步前端 service、hook、type。

### 修改模拟交易

必须读：

- `docs/backend/domain/模拟交易.md`
- `docs/backend/flows/模拟纸盘交易流程.md`
- `core/trading/`
- `api/routes/trading*.py`
- trading 相关测试

注意：

- 这是模拟纸盘交易，不是真实交易。
- 订单、账户、持仓、权益快照要保持一致性。
- 不要引入真实交易所下单行为。

### 修改数据层

必须读：

- `docs/backend/domain/数据层.md`
- `docs/backend/crosscut/数据库与存储.md`
- `core/data/`
- `core/persistence/`

注意：

- 明确数据频率、时间范围、时区和缺失数据处理。
- 不要硬编码私有数据路径或凭证。
- CSV 导入和 Parquet 存储要保留边界校验。

### 修改前端 API 调用

必须读：

- `docs/frontend/domain/API服务层.md`
- `docs/frontend/domain/ReactQueryHook层.md`
- `web/src/services/`
- `web/src/hooks/`
- `web/src/types/`

注意：

- service 返回前端友好的数据结构。
- hook 负责缓存、轮询、mutation 和提示。
- 不要在页面中重复写请求状态管理。

### 修改前端页面或组件

必须读：

- `docs/frontend/domain/页面层.md`
- `docs/frontend/domain/UI组件库.md`
- 对应页面文件
- 对应组件、hook、store、type

注意：

- 保持现有视觉和交互模式。
- loading/error/empty/disabled 状态要完整。
- 检查常见桌面和移动宽度下的布局。

## 7. 无效工作判定

以下行为通常视为无效工作：

- 未读取对应文档和源码就修改核心模块。
- 为修 UI 问题直接改后端核心数据结构。
- 为绕过 TypeScript 错误滥用 `any` 或放宽核心类型。
- 为让测试通过删除断言、跳过测试或降低校验。
- 在 API 层复制 `core/` 已有业务逻辑。
- 在前端页面重复写 `services/` 或 `hooks/` 已有的数据请求逻辑。
- 用模拟数据替代真实 API 契约，除非任务明确是原型。
- 修改交易、回测、评分逻辑但不做最小回归验证。
- 新增依赖、升级依赖或切换构建体系但未说明必要性。
- 触碰真实交易、凭证、账户状态或生产部署。

## 8. 验证要求

根据改动范围选择最小有效验证。

### 后端

```bash
cd MyQuant
python -m pytest tests
```

范围较小时，优先运行对应测试文件或测试目录；涉及包导入、CLI、API 生命周期时，再扩大验证。

两条闭环纪律（缺一即验证不完整）：

- **合入主干后重启服务**：执行 `./scripts/restart.sh` 并确认三服务
  起来——常驻进程不感知磁盘代码变更，不重启则后续验证与使用跑的
  都是旧代码。
- **合入后回销对应 feature_requests 单**：修复/交付合入主干后，由
  实施方对覆盖该诉求的需求单 PATCH `status=resolved` 并附
  `resolution.source`（对应 change 名/出处）；用户裁决不处理的单
  PATCH `status=wont_fix`。

### 前端

```bash
cd MyQuant/web
pnpm run lint
pnpm run test
pnpm run build
```

UI 改动在可行时启动开发服务器，并检查相关页面在常见桌面和移动宽度下没有重叠、溢出或不可操作状态。

## 9. 交付要求

最终交付必须说明：

- 改了哪些文件。
- 行为发生了什么变化。
- 做了哪些验证，结果如何。
- 哪些内容没有验证，以及剩余风险。

不要只说“已完成”。涉及交易、回测、评分、数据、API 契约的变化，必须说明影响范围。
