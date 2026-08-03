# 币安 U本位永续合约 demo mode 接入规划（v3·已冻结）

> **v3 修正（2026-06-21，基于官方文档纠正）**：原 v2 用 testnet（testnet.binancefuture.com），**改为 demo mode（demo-fapi.binance.com）**。
> - **端点**：`testnet.binancefuture.com` → **`demo-fapi.binance.com`**
> - **python_binance**：`Client(testnet=True)` → **`Client(demo=True)`**（源码 base_client.py:307 切 `FUTURES_DEMO_URL`）
> - **凭证机制**：独立 testnet 账户 → **主账户 → Binance Demo Trading → API Key Management 创建 key**
> - **理由**：① testnet 正被币安废弃（旧 key 删除）；② CCXT #26487 旧 testnet 端点有**错误路由到实盘**的 bug（严重风险）；③ demo mode **行情接近实盘**（解决原 R6「testnet 行情偏差、不评估策略」局限）、功能/过滤器与实盘完全一致、当前官方推荐。
> - **已交付代码已改**：`credentials.py`(SERVICE_DEMO/demo 字段)、`binance_client.py`(Client(demo=True))、类名 `BinanceDemoClient`/`BinanceDemoGateway`，105 测试全绿。
> - 正文残留 testnet 字样为 v2 历史，**以本块为准**；原 R6「testnet 行情偏差」在 demo mode 下**不再成立**（demo 行情接近实盘，策略验证有意义）。
>
> **状态：推理链 v3 已冻结**。实现已启动（引擎层 9 模块完成，见文末实施进度）。
> **v2 相对 v1 的变更**（经架构评审 + 用户决策）：
> - **持仓**：HEDGE 双向 → **单向 One-Way Mode**（偏差3 决策）。理由：单仓=模拟交易持仓模型→实盘与模拟可对照验证；方向由 `DNA.direction` 自适应（long/short/mixed 三值全支持），不阉割方向能力。
> - **时序**：未明确 → **bar 轮询模型**（偏差2 决策）。理由：模拟是 bar 驱动，`judgment` 决策语义/SL-TP 触发语义只在 bar 闭合模型下成立；tick 实时会全部失效。
> - **复用边界**：原"共享信号+决策+风控"过宽 → **精确化为只复用纯计算**（偏差1 决策）。风控是 VirtualAccount 有副作用方法（`account.py:266`）不可 import；shift 时序保证在 pipeline 层（`pipeline.py:197`）需实盘自建。
> - **实施顺序**：插入"第 0 步 spike"消除 python_binance 1.0.37 不确定性。

---

## 任务定义
新建**独立的实盘交易模块**（后端 `core/live_trading/` + 独立 API `/api/live-trading/*` + 独立前端页 `/live-trading` + 独立 DB 表 `live_trading_task`），接入币安 USDT-M futures **testnet**（模拟资金），**单向持仓 One-Way Mode**，**方向由策略 `DNA.direction` 自适应**（支持 long/short/mixed），**bar 轮询时序**。与现有模拟交易**并列**，仅共享纯计算层（信号生成 executor / 决策规则 judgment / DNA·GARCH·types，import 复用）；**模拟交易零改动**。

触及 AI_CODING_GUIDE §1 红线（实盘执行），用户已确认边界=层3 testnet（非真实资金，层4 真实资金本轮排除）。

## 关键设计决策（v2·用户确认）
1. **独立模块**（非执行层切换）：实盘是并列产品模块，模拟交易不动。理由：风险物理隔离 + 产品心智分离 + 独立演进。
2. **单向持仓 One-Way Mode**：账户级单向（持仓为净值，`positionSide=BOTH`，任一时刻一个方向）。**方向自适应**：读取 `DNA.direction` 作 `allowed_direction`，复用 `judgment.py:88` 过滤逻辑，完整支持 long/short/mixed。不用 Hedge 双仓、不做对冲。
3. **bar 轮询时序**：实盘按 bar 闭合驱动决策+下单（与模拟同模型）。信号在 bar N 闭合生成 → bar N+1 开盘下单（等价 shift(1)，由实盘 pipeline 自建 pending 机制实现）。代价=信号延迟一个 bar，testnet 可接受。
4. **复用边界精确**：共享=纯计算（executor 信号 / judgment 决策纯函数 / DNA·GARCH·prediction / types 数据类 / order_generator 定价计算）；**独立**=状态管理/执行/OMS/风控/pipeline 时序/API/前端/DB。
5. **真实止损止盈单**：STOP_MARKET/TAKE_PROFIT_MARKET 交易所托管（One-Way：`positionSide=BOTH` + `closePosition=True`）。
6. **状态交易所为准**：sync_state() 周期拉 balance/position，本地是缓存不累加。

## 现状研究（subagent 证据 + 架构评审）
**现有执行边界（不动）**：VirtualAccount 5 写方法（account.py:121/153/181/217/420）+ OrderManager 本地撮合（order_manager.py:56-77，用 bar_high/low fill 判定）+ DecisionPipeline bar 驱动编排（pipeline.py:94-254）+ TradingRunner 硬绑定（runner.py，直接 new VirtualAccount/Pipeline，表名硬编码，无执行后端接口抽象）。

**评审关键发现**（影响设计）：
- 持仓单仓互斥（`account.py:64` Optional[Position]），反向"先平后开"（`judgment.py:72`）→ 实盘 One-Way 与之一致，可对照。
- `direction="mixed"` 是择时二选一（`judgment.py:84`）非对冲 → 实盘完整支持三值，单仓不并存。
- 决策依赖本地即时状态（`account.py:466` get_state 读内存），无外部感知 → 实盘需"交易所为准 + 周期同步"。
- shift(1) 时序保证在 pipeline 的 `pending_decision` 暂存（`pipeline.py:197,243`），非纯计算层 → 实盘 pipeline 自建等价机制。
- 风控 `check_sl_tp`（`account.py:266`）是逐 bar 后扫描、有副作用（命中直改 balance/position），**不可 import** → 实盘风控重写为"下单前拦截"。
- 崩溃恢复哲学"crash→error→stop"（`runner.py:317`）→ 实盘不能照抄（交易所端止损止盈单可能残留）。
- **计算引擎能力评估**：对 bar 级低频规则策略实盘**够格**（确定性+未来函数防护+脏数据安全侧 fillna(False)+成本过滤 judgment.py:62+反手控制 min_hold_bars 齐全）。3 边界：天花板=bar 级规则（非高频）；entry 无成本/频率过滤（可改进）；够不够取决于模型（bar 够/tick 不够）。
- python_binance 1.0.37 支持 `Client(testnet=True)`（切 testnet.binancefuture.com/fapi，源码确认）。下单 `futures_create_order`/查询 `futures_account`/`futures_position_information`/杠杆 `futures_change_leverage`/持仓模式 `futures_get_position_mode`+`futures_change_position_mode`/资金费 `futures_funding_rate`。风险码：-1021 时间同步、-1111 精度、-2010 保证金、-2022 reduceOnly（HEDGE 禁用，One-Way 可用于平仓单）。

---

## 推理链（七环·v2 冻结）

### 环1·任务定义
新建独立实盘模块，单向 One-Way 持仓接入 USDT-M testnet，方向由 DNA.direction 自适应，bar 轮询时序，与模拟并列，仅共享纯计算层。模拟零改动。

### 环2·现状定位（架构设计问题）
模拟交易与实盘是不同风险等级/心智的独立产品，不应执行层切换耦合。当前执行逻辑硬编码在 VirtualAccount/Pipeline/Runner（无独立实盘模块，无执行后端接口抽象）。模拟交易整套链路（pending 暂存、bar_high/low fill、GARCH 按 bar observe、单仓记账）依赖"逐 bar 严格时序"，实盘要复用 `judgment` 决策语义就必须采用同模型（bar 轮询）。需新建独立模块而非改造现有。

### 环3·解决策略
- **独立模块** `core/live_trading/`：LiveTradingRunner + BinanceTestnetGateway + LiveAccount + RiskGuard + 实盘 OMS + 实盘 pipeline。
- **单向 One-Way Mode**：账户级单向，下单矩阵（见行为规格）；`positionSide=BOTH`；方向由 `DNA.direction` 决定；反手"先平后开"两步（与模拟一致）。
- **bar 轮询编排**：实盘 pipeline 自建"bar 闭合才决策 + pending 暂存"的 shift 等价机制，不复用模拟 pipeline。
- **真实止损止盈单**：开仓同挂 STOP_MARKET+TAKE_PROFIT_MARKET（One-Way：positionSide=BOTH+closePosition=True），平仓撤单。
- **状态交易所为准**：sync_state() 周期拉 balance/position，不本地累加。
- **下单前风控**：实盘专属，下单前拦截（名义额/频率/日亏损熔断），不复用 `check_sl_tp`。
- **纯计算共享**：import executor/judgment/DNA/GARCH/types/order_generator 定价。
- 排除替代：①不 ExecutionGateway 切换（用户否决）②不复制模拟全链路（重复）③不改 VirtualAccount（污染回测）④不用 HEDGE 双仓（选单仓，可对照）⑤不用 tick 实时（选 bar 轮询，保时序语义）。

### 环4·范围边界
**纳入**：core/live_trading/ 全套 + 单向 One-Way 矩阵 + bar 轮询编排 + positionMode 初始化 + 凭证管理 + 下单前风控 + 真实止损止盈单（One-Way）+ 实盘 OMS/独立 pipeline + 独立 API + 独立前端页 + 独立 DB 表。
**排除**：①模拟交易任何改动（零回归）②真实资金（层4）③主网连接④HEDGE 双仓/对冲保证金逻辑⑤tick 实时模型⑥前端实盘页复杂图表（后续）⑦移动止损（v1 固定止损止盈，后续）。

### 环5·行为规格
- **One-Way 下单矩阵** `[测试验证]`：
  - 开多：side=BUY, positionSide=BOTH, qty>0
  - 平多：side=SELL, positionSide=BOTH, reduceOnly=True, qty=多头持仓量
  - 开空：side=SELL, positionSide=BOTH, qty>0
  - 平空：side=BUY, positionSide=BOTH, reduceOnly=True, qty=空头持仓量
- **方向自适应** `[测试验证]`：读取 `DNA.direction` 作 `allowed_direction`（long/short/mixed），复用 `judgment.evaluate` 过滤；mixed 时择时二选一，单仓不并存。
- **反手"先平后开"** `[测试验证]`：持多遇空信号 → 先发平多单（reduceOnly）→ 成交后下 bar 再开空，与 `judgment.py:72` 两步语义一致（不一笔净额翻转）。
- **bar 轮询时序** `[测试验证]`：bar 闭合才决策+下单；信号 bar N 生成 → bar N+1 开盘下单（实盘 pipeline 自建 pending 暂存，等价 shift(1)，杜绝未来函数）。
- **positionMode 初始化** `[测试验证]`：启动查询 `futures_get_position_mode`，非 One-Way 则 `futures_change_position_mode`（双向→单向）。
- **真实止损止盈单** `[测试验证]`：开仓同挂 STOP_MARKET+TAKE_PROFIT_MARKET（positionSide=BOTH+closePosition=True），价格由共享 SL/TP 定价计算（复用 pipeline.py:75-92 同算法）；平仓撤单。
- **状态一致性** `[集成测试]`：sync_state() 周期拉 `futures_account`+`futures_position_information`，本地缓存以交易所为准；两次 sync 间基于缓存决策。
- **下单前风控** `[测试验证]`：单笔最大名义额、下单频率（最小间隔）、日亏损熔断、保证金占用上限；任一触发拒绝下单并告警。testnet 也强制。
- **崩溃恢复孤儿单** `[测试验证]`：重启时查询交易所端未成交/残留的 STOP/TP 单，按策略状态撤单或认领，不重复挂单。
- **独立 API** `/api/live-trading/*`（任务创建/控制/查询/风控配置）+ **独立前端** `/live-trading`（单向持仓+任务+风控）`[集成测试]`。
- **模拟交易零回归** `[测试验证]`：现有测试全绿；共享层（executor/judgment）被实盘场景测出 bug 修复时走全量回归（属共享代码演化，不违反零改动）。

### 环6·风险披露
- R1〔确定/高〕错连主网：testnet 专用 key + testnet=True + 首调 `futures_account` 核对模拟余额（三重保险）。
- R2〔确定〕时间同步 -1021：启动同步 `futures_time()`，偏移>1s 校正。
- R3〔确定〕精度 -1111：按 `futures_exchange_info` tickSize/stepSize 取整。
- R4〔确定〕状态漂移：bar 轮询下两次 sync 间基于本地缓存决策，可能重复开/反向开 → 缓解：下单前强制 sync 持仓 + 决策用最新同步态。
- R5〔确定〕bar 信号延迟：信号延迟一个 bar（如 1h bar 延迟 1h 才下单）→ testnet 可接受；实盘滑点需认知（bar 模型固有代价）。
- R6〔确定〕testnet 行情偏差：验证下单逻辑 OK，不评估实盘表现。
- R7〔不确定→spike 消除〕python_binance 1.0.37 旧版坑：第 0 步 spike（装环境+连 testnet+发 1 单+查持仓）30 分钟消除；兜底手动覆盖 FUTURES_API_URL 或升级 binance-futures-connector。
- R8〔确定/高〕凭证泄露：加密本地 + .gitignore + 权限最小化（禁提现）。
- R9〔确定〕崩溃恢复孤儿单：模拟是 crash→stop，实盘不能照抄 → 重启查交易所端残留单处理（见行为规格）。
- R10〔确定〕共享层演化连带回归：executor/judgment 修复连带影响模拟 → 缓解：共享层改动走全量测试。

### 环7·实施顺序（依赖排序，每步独立可验证可回退）
0. **spike 验证**（前置依赖）：装 1.0.37 + 连 testnet + 查 position mode + 发 1 笔 One-Way 测试单 + 查持仓，消除 R7 不确定性。
1. 凭证管理 + 配置（testnet key 加密存储 + .gitignore + 权限校验）。
2. BinanceClient 封装（testnet 连接 + 时间同步 + 精度取整 + 限频 + positionMode 查询/切换）。
3. core/live_trading/ 模块骨架 + 数据类（LiveAccount 独立状态模型，不复用 VirtualAccount；LiveOrder/Fill）。
4. BinanceTestnetGateway（One-Way 下单矩阵 + 查询 + 真实止损止盈单 positionSide=BOTH+closePosition）。
5. RiskGuard 下单前拦截（名义额/频率/日亏损熔断/保证金上限，不复用 check_sl_tp）。
6. 实盘 OMS（订单生命周期状态机：pending/filled/partial/canceled/rejected + 幂等 + 孤儿单查询）+ 实盘 pipeline（bar 轮询编排 + pending 暂存 shift 等价 + import executor/judgment）。
7. LiveTradingRunner（独立任务循环 + 崩溃恢复孤儿单处理 + 心跳）。
8. 独立 API `/api/live-trading/*`（任务创建/控制/查询/风控配置）。
9. 独立 DB 表 `live_trading_task`（任务状态/持仓/订单/equity 快照/审计）。
10. 独立前端页 `/live-trading`（单向持仓 + 任务 + 风控参数 + 凭证设置）。
11. testnet 端到端验证（模拟资金跑通：bar 闭合→信号→单向下单→止损止盈托管→平仓→状态同步）。

## 前端独立页面参考（/live-trading）
```
侧边栏新增"实盘交易"入口（与"模拟交易"并列）
页面: [Testnet连接状态] [钱包余额] [凭证设置]
     [当前持仓: 方向(多/空) 数量 开仓价 未实现盈亏]  ← 单仓（One-Way）
     [实盘任务列表: 新建/监控/停止]
     [风控参数: 单笔最大/日亏损熔断/下单频率]
     [止损止盈托管单状态]
```

## 共享边界（v2 精确化）
| 共享（import 复用·纯计算） | 独立（新建） |
|---|---|
| 信号生成 `executor.py` | LiveTradingRunner（bar 轮询编排） |
| 决策规则 `judgment.py evaluate`（纯函数） | BinanceTestnetGateway（One-Way） |
| DNA / GARCH / prediction | LiveAccount（交易所为准状态） |
| types.py 数据类 | RiskGuard（下单前拦截） |
| order_generator.py 定价计算（SL/TP 价算法） | 实盘 OMS（订单生命周期，不复用 OrderManager） |
| | 实盘 pipeline（bar 轮询时序 + pending 暂存，自建 shift 等价） |
| | /api/live-trading/* |
| | /live-trading 前端页 |
| | live_trading_task DB 表 |
| | 凭证加密管理 |

> **明确不共享（v2 修正）**：①风控 `check_sl_tp`/`check_liquidation`（VirtualAccount 有副作用方法，实盘重写为下单前拦截）②持仓记账 VirtualAccount（实盘 LiveAccount 交易所为准）③OMS OrderManager（本地 bar_high/low 撮合，实盘真实生命周期状态机）④pipeline 时序（pending 暂存，实盘自建）⑤Runner 编排（硬绑定，实盘独立）。

## 计算引擎能力评估结论（支撑"够格复用"）
对 bar 级低频规则策略实盘**够格**：确定性（random 仅离线进化）、未来函数防护（shift+pending）、脏数据安全侧（fillna False）、成本过滤（judgment.py:62 exit 手续费）、反手控制（min_hold_bars）齐全。3 边界需认知：①天花板=bar 级规则（非高频，是定位）②entry 无成本/频率过滤（实盘可改进点）③够不够取决于模型（bar 轮询够，本规划已选 bar 轮询）。
