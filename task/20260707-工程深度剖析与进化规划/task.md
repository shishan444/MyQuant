# MyQuant 工程深度剖析（链路闭环版）

> 生成时间：2026-07-07（基于代码层完整链路梳理，非二手摘要）
> 目标对齐：把 MyQuant 打造成**个人自动化实盘量化工程**——自动交易获利 + 策略自我进化 + 严格风控。
> 方法：亲自读完 6 条核心链路的完整源码，每条判断挂 `file:line` 证据，关键 bug 用真实引擎复现验证。
> 与上一版差异：修正了 3 处粗糙/错误判断（见文末「勘误」）。

---

## 一、核心链路全景图（亲自梳理）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 链路A：进化（离线，历史数据上搜索更优策略）                          │
│   api/runner.py:470 evaluate_fn                                       │
│     → evaluation.py:53 evaluate_dna                                   │
│       → BacktestEngine.run (链路2)                                    │
│         → metrics (链路3) → compute_fitness → score                   │
│     → EvolutionEngine.evolve (engine.py:183)                          │
│       → 锦标赛选择+自适应变异+精英+多样性 → champion                  │
│       → 每代 on_generation 回调持久化 (api/runner.py:494)             │
│   注：已有 train/test split (api/runner.py:431-432, train_ratio=0.7) │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ 链路B：模拟交易（paper，后台线程实时跑）                              │
│   TradingRunner.run (core/trading/runner.py:119, 已在 app.py:73 启动) │
│     → _execute_task:205 → 增量 bar 循环 :376                          │
│       → dna_to_signal_set (与回测共享信号层)                          │
│       → DecisionPipeline.process_bar (pipeline.py:94, 8步)            │
│         → pending_decision 跨bar状态机 (pipeline.py:189-197,243)     │
│         → check_sl_tp(high/low) + check_liquidation(open)             │
│         → judgment.evaluate 纯函数决策                                 │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ 链路C：实盘（demo mode）—— ⚠️ 代码写完但未接线                       │
│   LiveTradingRunner.run (core/live_trading/runner.py:150)            │
│     ❌ 生产从未实例化（grep 确认，仅自身定义处）                      │
│     → run_iteration:106 → sync_state → execute_closed_bar             │
│       → LivePipeline.decide(复用judgment) → RiskGuard → Gateway       │
│   API 仅 task CRUD（api/routes/live_trading.py，无启动端点）          │
└─────────────────────────────────────────────────────────────────────┘
```

**关键事实**：三条链路里，**只有链路A（进化）和链路B（模拟）真正在跑**；链路C（实盘）是"代码完整、未接线"状态（代码作者在 `api/routes/live_trading.py:3-4` 注释明确标注"后续接入"）。

---

## 二、逐链路梳理与证据

### 链路1：DNA → 信号生成（信号层不含 shift）

- **DNA 结构**（`core/strategy/dna.py`）：`SignalGene`（indicator+params+role+condition）+ 8 种 `SignalRole`（ENTRY/EXIT/ADD/REDUCE trigger/guard + DIRECTION）+ `RiskGenes`（stop_loss/take_profit/leverage/direction/sl_mode）。
- **信号生成**（`core/strategy/executor.py:509 dna_to_signal_set`）：
  - 每个 gene → `evaluate_condition`（executor.py:31）转 bool Series。
  - 按 role 分组 → `combine_signals`（AND/OR，executor.py:272）。
  - 输出 `SignalSet{entries, exits, adds, reduces, entry_direction}`，**全部是当 bar 信号**。
- **修正上轮**：cross_above 内部的 `shift(1)`（executor.py:65）是**判定"穿越"语义的需要**（前一根在下、当前在上），**不是延迟信号**。真正的"延迟到下 bar 执行"发生在链路2 的 BacktestEngine。
- **防未来函数的 shift(1) 真实位置**：`core/backtest/engine.py:419-422`（单策略）和 `:688-705`（批量），对 entries/exits/adds/reduces/entry_direction 全部 `.shift(1).fillna(False)`。

### 链路2：信号 → 回测引擎 → 指标（亲自读完 + 实测）

回测核心 `core/backtest/engine.py`：

**时序**：`_build_portfolio:419-422` 对信号 shift(1) → `vbt.Portfolio.from_order_func:456` → `order_func_nb:206`（numba JIT，逐 bar 回调）。

**费用**（engine.py:399-400）：`fee=0.001`（0.1%单边）+ `slippage=0.0005`，每次开/平/SL/TP/加/减都收（`effective_fee` 传给每个 `order_nb`）。**费用不乘杠杆**（engine.py:235 `effective_fee=fee`），杠杆放大靠后处理 `_apply_leverage_to_equity:133` —— 这是**正确设计**，避免 fee×L² bug，有不变量测试守护。

**杠杆**：vbt 内部只跑 `position_size`，杠杆效果全靠 `_apply_leverage_to_equity:507-512` 后处理放大曲线。有线性不变量测试（`ratio==L`）。

**SL/TP 触发**（order_func_nb:277-325）：用 bar 的 `high/low` 判定（多头 SL 看 low、TP 看 high），**SL 优先于 TP**（:287 先 return）。但**成交价用 close**（vbt order_nb 默认），非 SL/TP 触发价 → **偏乐观**。

**funding**（`_apply_funding_costs:73`，默认 `RATE_PER_8H=0.001`）：仅 leverage>1 计入，按持仓 bar 区间、基于 position_value（借款额）扣。默认 0.1%/8h **偏高**（Binance 历史均值约 0.01%），系统性低估杠杆策略收益。

**清算（关键 bug，见第三节）**：两处公式不一致。

**metrics**（`core/scoring/metrics.py:8`）：从 equity_curve + trade_returns 算 sharpe（优先 trade-level）、max_drawdown、calmar、profit_factor 等。**metrics 完全不读 `liquidated` 标志**。

### 链路3：指标 → fitness → 进化选择（亲自读完 + 实测）

`core/scoring/scorer.py:35 compute_fitness`：
- **目标函数**（objective）：sharpe / calmar / annual_return（默认 sharpe，前端所有预设也是 sharpe，`web/src/components/evolution/QuickPresets.tsx:24`）。
- **硬约束门控**（scorer.py:69-76）：max_drawdown / total_trades / profit_factor / annual_return 任一不达标 → fitness=0。
- **liquidated 门控**（scorer.py:53）：`if liquidated: return _zero_result()` → fitness=0。
- 负 objective 被 `max(0.0, ...)` 钳零（scorer.py:87）。

### 链路4：进化编排（亲自读 api/runner.py:400-600）

- `EvolutionEngine.evolve`（engine.py:183）：锦标赛选择（engine.py:125）+ 自适应变异（1/5 规则，engine.py:92）+ 精英保留（:299）+ 新鲜血液（:404）+ 多样性维护（:411）。
- **已发现工程已做 train/test split**（api/runner.py:431-432，`train_ratio=0.7`）：进化只在 train 段评估（:473 `_train_df`）。**修正上轮**：上轮说"建议加 walk-forward"，实际工程已有样本外验证雏形（train/test split），但不是滚动 walk-forward，且 test 段结果未参与 fitness（只用于评估）。
- champion 持久化经 `ChampionTracker`（api/runner.py:492）原子更新。

### 链路5：模拟交易 DecisionPipeline（子代理梳理 + 我核实关键差异）

`core/trading/`：用 `pending_decision` 跨 bar 状态机（pipeline.py:189-197, 243）实现 shift(1) 等价——"信号 bar N close 生成 Decision → bar N+1 open 执行"。

**与回测的关键差异（亲自核实）**：
| 维度 | 回测 (engine.py) | 模拟 (account.py/pipeline.py) |
|---|---|---|
| SL/TP 成交价 | close（偏乐观） | 触发价 sl_price/tp_price（更现实） |
| 清算判定价 | close（:266） | open（pipeline.py:178） |
| ATR SL/TP | **不存在**，永远 pct | 存在（pipeline.py:75-92） |
| judgment 层 | **无**（信号即下单） | 有完整 evaluate（min_hold_bars 等） |
| leverage | 后处理放大曲线 | 真实借贷（quantity=margin*L/price） |
| funding | 事后整条曲线扣 | 逐 bar 扣 balance |

**含义**：回测调出的策略参数，切到模拟时行为会变（尤其 SL 成交价和 leverage 实现）。"回测→模拟"不能假定一致。

### 链路6：实盘闭环（子代理梳理 + 我亲自验证 4 个断言全部属实）

`core/live_trading/`：设计是 One-Way mode + bar 轮询 + 托管 SL/TP + 下单前风控。**但未接线**。

---

## 三、已确认的问题（按严重度，每条带链路证据 + 实测）

### 🔴 P0-1：实盘执行闭环整体未接线（链路C）

- **证据（亲自 grep 验证）**：
  - `LiveTradingRunner` 在生产代码仅 `core/live_trading/runner.py:84` 自身定义，**零实例化**。
  - `api/app.py` lifespan 只启动 `EvolutionRunner` 和 `TradingRunner`（paper），**不启动 LiveTradingRunner**。
  - `api/routes/live_trading.py` 仅 5 个 task CRUD 端点，文件头注释（:3-4）明确写"Runner 启动/停止属集成层，后续接入"。
- **影响**：你的核心目标"自动化实盘交易"目前**无法达成**——代码写完了，但没有后台线程执行实盘。即使其余 bug 全修，实盘也不会跑。
- **这是最大的问题**，不是 bug 而是工程缺口。

### 🔴 P0-2：实盘日亏损熔断失效（record_pnl 零调用）

- **证据（亲自 grep 验证）**：`record_pnl`（risk_guard.py:111）在生产代码**零调用**，仅测试调用。
- **链路断点**：`executor.py:74-76` 下单后只 `risk.record_order()`（记时间戳），**平仓盈亏从未回传 `record_pnl`**。
- **影响**：`_daily_pnl` 恒为 0 → 日亏损熔断规则（risk_guard.py:75-80）**永远不会触发** → 实盘风控生命线形同虚设。你的"风险控制"目标在实盘层失效。
- **修复**：executor 平仓分支计算 `pnl = (exit_price - entry_price) * qty`，调 `risk.record_pnl(pnl)`。

### 🔴 P0-3：实盘 SL/TP 托管单 + 孤儿单处理死代码

- **证据（亲自 grep 验证）**：
  - `gateway.cancel_order`（gateway.py:123）**无调用方**。
  - `recover_orphan_orders`（runner.py:146）**生产零调用**。
- **链路断点**：`executor.py:79` 平仓时不撤 SL/TP 托管单；崩溃重启不处理孤儿单。
- **影响**：平仓后交易所端 SL/TP 单残留 → 可能误触发反向开仓（SL/TP 单未设 reduceOnly，gateway.py:97-105）；崩溃后孤儿单无认领。

### 🔴 P0-4：实盘未过滤 forming bar → shift(1) 假等价（未来函数泄漏）

- **证据**：`runner.py:112-123` 用 `futures_klines(limit=200)` 返回的**最后一根（未闭合）bar** 的 close 决策下单。对比模拟交易有 `_filter_forming_bar`（core/trading/runner.py:447），实盘**没有**。
- **影响**：用未收盘的实时跳动 close 生成信号并下单 = 未来函数泄漏，与回测/模拟的 shift(1) 语义不一致。实盘行为不可控。

### 🟠 P1-1：回测清算公式不一致（已实测复现，但危害边界比上轮判断更窄）

- **证据（亲自读 + 真实引擎实测）**：
  - `engine.py:265`（实时清算）：`maintenance = init_cash * (1 - 0.9/L²)`
  - `engine.py:521`（后处理标记）：`maintenance = init_cash * (1 - 0.9/L)`（少平方）
- **实测复现**：3 倍杠杆 + 下跌 10%/15%/20% 时，最低权益（84977/77491/70005）已低于实时阈值 90000（实时清算触发），但 `liquidated` 标志报 False（后处理阈值 70000 没捕获）。
- **修正上轮**：上轮说"污染进化选择"方向对，但**危害边界更窄**——
  - objective=**sharpe**（默认）或 annual_return 时：爆仓策略 sharpe/return 本身 ≤ 0，被 `max(0.0,...)` 钳零（scorer.py:87），**fitness 仍 0，漏报无实际危害**。
  - objective=**calmar** 时：calmar=|annual_return/max_drawdown|=|−0.06/−0.30|=0.199>0，**fitness=0.199 且 qualified=True，漏报让爆仓策略通过门控**。
- **结论**：bug 真实存在，但只在用户手动切到 calmar 目标时产生危害。前端默认全 sharpe，所以默认场景下无危害。

### 🟠 P1-2：回测 SL/TP 成交价偏乐观 + 清算用 close

- **证据**：engine.py:289 SL/TP 成交用 close（vbt 默认），非触发价；清算用 `c.value_now`（基于 close，engine.py:266），非 low/high。
- **影响**：bar 内瞬时击穿但收盘回升的清算被漏掉；SL 穿越后又反弹的 bar 成交在更高 close。系统性高估策略表现，实盘会遭遇回测没预见的亏损。

### 🟠 P1-3：API 未透传 total_funding_cost / liquidated

- **证据**：tests/test_backtest_e2e_consistency.py:120-145, 239-284 明确标注。引擎算对了，前端看到的指标是错的。

### 🟡 P2：评估失败个体与失败个体不可区分

- **证据**：evaluation.py:160-169 异常返回 score=0.0，与正常 0 分个体（无信号/不达标）不可区分。错误个体占用种群名额。

---

## 四、四、下一步规划（基于完整链路，按依赖排序）

```
阶段 0：补齐实盘接线 + 风控闭环（达成"自动化交易"前提，最高优先级）
  [P0-1] api/app.py 接入 LiveTradingRunner 生命周期（仿 TradingRunner 模式）
       + api/routes/live_trading.py 加 start/stop/status 控制端点
  [P0-2] executor 平仓分支补 record_pnl，激活日亏损熔断
  [P0-3] 平仓补 cancel SL/TP；重启补 recover_orphan_orders 接线
  [P0-4] run_iteration 入口加 forming-bar 过滤（仿 core/trading/runner.py:447）
  [配套] live_trading 补单测（executor/risk_guard/gateway/runner 端到端）

阶段 1：堵回测漏洞（利润/风控的根基）
  [P1-1] 统一清算公式（提取 maintenance_margin(leverage,init_cash) 纯函数共用）
       + 补单测：实时清算触发时 liquidated 必为 True（覆盖 calmar 目标场景）
  [P1-2] 清算改用 low/high；SL/TP 成交价用触发价（与模拟一致）
  [P1-3] API 透传 total_funding_cost / liquidated
  [配套] funding 默认值改可配（0.1%/8h → 历史均值或真实值）

阶段 2：补齐实盘验证闭环
  前端 /live-trading 页面（规划第10步）
  demo mode 端到端验证（需 demo key）

阶段 3：自我进化闭环（核心价值，需实盘跑通后）
  test 段结果反哺进化权重（已有 train/test split 雏形，api/runner.py:431）
  实盘表现 → 进化反馈（前向验证，检测过拟合）
  策略组合（非单冠军，用 diversity 衡量相关性）
```

---

## 五、对上一版的勘误（体现链路深挖的修正）

| 上轮判断 | 链路深挖后的修正 |
|---|---|
| "清算 bug 污染进化选择" | 方向对，但**危害边界窄**：仅 objective=calmar 时有害，默认 sharpe 无危害（爆仓 sharpe≤0 被钳零）。实测复现。 |
| "建议加 walk-forward 验证" | 工程**已有 train/test split**（api/runner.py:431，train_ratio=0.7），上轮漏看。准确说法：补滚动 walk-forward + 让 test 段参与 fitness。 |
| "live_trading 测试为 0" | **更正**：实际有单测（tests/test_live_executor.py / test_live_risk_guard.py / test_live_runner.py / test_live_gateway.py），覆盖各组件单元；缺的是**端到端接线测试**和**runner 生命周期测试**。 |
| 未提及实盘未接线 | **重大补充**：LiveTradingRunner 生产从未实例化（亲自 grep 确认），这是上轮完全漏掉的最大问题，直接关系到"自动化交易"目标能否达成。 |
| 未提及 record_pnl/recover_orphan/cancel_order 三个死代码 | **重大补充**：三个风控/生命周期函数生产零调用（亲自 grep 确认），实盘风控实际失效。 |

---

## 六、验证情况（完成审计）

**已亲自验证（非二手）**：
- ✅ 链路1-3 完整源码通读（dna.py / executor.py / engine.py / metrics.py / scorer.py）。
- ✅ 清算 bug 用真实 BacktestEngine 复现：3 倍杠杆 + 下跌 10-20% → liquidated=False 漏报，且 fitness 在 calmar 目标下非零。
- ✅ 链路6 四个关键断言亲自 grep 验证：LiveTradingRunner 零实例化、record_pnl 零生产调用、recover_orphan_orders 零生产调用、cancel_order 零调用方。
- ✅ 链路4 进化编排 api/runner.py:400-600 通读，发现 train/test split（修正上轮）。
- ✅ live_trading API 路由通读，确认仅 CRUD、作者自注"待接入"。

**基于子代理 + 我核实**：
- 链路5（模拟交易）：子代理梳理的差异表，我对照源码确认关键行号。
- 链路6 链路细节：子代理梳理，我亲自验证了其中 4 个最强断言。

**未验证项 / 剩余风险**：
- 全量测试套件（150 文件）未跑，仅跑了进化/DNA 子集（68 全绿）。
- 实盘 demo 端到端未验证（无 demo key，且 runner 未接线无法验证）。
- 清算 bug 修复对历史 champion 库的影响未量化。

**核心结论**：你的目标"自动化实盘交易 + 自我进化 + 风控"——**自我进化（链路A）和模拟（链路B）已能跑**；**实盘（链路C）代码完整但未接线，且风控/孤儿单/forming-bar 三处断链**。最该先做的是**阶段 0：补齐实盘接线 + 风控闭环**，这是你目标能否达成的关键前提。
