# verify-myquant-capability-integrity · 发现登记清单

容器期累积工件（两级处置：口径分歧→本容器裁决锁定；实现缺陷→登记证据独立跟进，不修生产代码）。

## 实现缺陷候选（独立 fix 分支跟进）

| # | 发现 | 证据锚点 | 影响面 | 状态 |
|---|---|---|---|---|
| D-1 | 清算盯市晚一根：vbt `c.value_now` 在 order 执行时刻为前一根收盘估值，`value_now < maintenance` 检查因此晚一根触发强平 | `tests/reference/contract.py` D-1 注记；哨兵 `test_cross_validation.py::test_liquidation_timing_sentinel`；实证 liquidation_edge lev5 seed11 trade0（当根 80051<80200 未触发，次根 79843 触发） | 高杠杆边界场景多担一根清算风险（真实损失放大） | 已锁定行为+哨兵；待独立 fix |
| D-2 | 负权益 annual_return 为复数：`(1+total_return)**(1/years)` 负底数浮点幂 Python 返回 complex（numpy 路径为 nan） | `core/scoring/metrics.py:70`；实证 liq-edge-lev5（engine 复数 0.76+4.44j vs ref nan）；比对器于穿零 case 排除该字段 | 权益穿零场景指标无意义值外泄（API 序列化层若 float() 将崩） | 比对排除+登记；待独立 fix |
| D-3 | 负权益 max_drawdown 突破值域：`(eq−cummax)/cummax` 于负 cummax 区发散，产出 <−1（实测 −2.43）；两实现同值同错（恒等式族抓「两实现同错」盲区的首例） | `core/scoring/metrics.py:77`；实证 liq-edge-lev5 双侧同值 −2.42919105 | 穿零场景风险指标失义；评分/展示层若消费此值产生误导 | 值域断言穿零降级+登记；待独立 fix |
| D-4 | DNA 环变性：`to_dict` 序列化 auto-wrap 单层 layers，`from_dict` 读回判为显式 MTF（is_mtf 翻转）→ **API 创建策略后 strategy_id 路径回测 500**（`MTF DNA requires dfs_by_timeframe`） | `core/strategy/dna.py:357-364`；哨兵 `test_four_entry_consistency.py::test_d4_sentinel_strategy_id_backtest_broken` | 用户主路径断裂（策略库→回测）；单元层全绿（mock 绕过信号层）——链路级 bug 首例实锤 | 哨兵锁定；待独立 fix（**高优先**） |
| D-5 | backtest(dna) 静默降级：不传 payload.symbol/timeframe 时按 "UNKNOWN"/"1d" 找盘失败 → 无告警走 legacy fallback（不做日期切片+逐 gene 指标路径）→ 口径漂移无感知 | `api/routes/strategies/backtest.py:95-140`；R-5 裁决注记 | 同入口两条数据加载路径指标不可比（实测 0.0141 vs 0.0075 同窗差异） | 测试注记锁定；待独立 fix |
| D-2b | NaN 泄漏产非法 JSON：`json.dumps` 默认放行 NaN 字面量，JS `JSON.parse` 抛 SyntaxError → 指标层泄漏 NaN 即前端整响应解析崩溃 | `tests/consistency/test_transport_invariance.py::test_nan_leak_produces_invalid_json`；`web/src/test/transport.test.ts` | 与 D-2 同族传输边界风险 | 风险面锁定；待独立 fix |

## 口径裁决记录（本容器锁定）

| # | 事项 | 裁决 | 锚点 |
|---|---|---|---|
| R-1 | funding 的持仓值口径：被测 `position_values = raw_equity − cash` 对空头持仓为负 → funding 反向增加权益 | 参照按「借入成本」\|pv\| 正确语义实现；空头+杠杆场景比对若分歧如实报 DiffReport（本批五场景未触发该组合，登记待裁决） | `tests/reference/reference_backtest.py` 模块注记 |
| R-2 | funding 逐 bar 扣减不传播：`adjusted[i] -= cost` 逐点独立，不滚动进后续 bar 权益 | 两实现口径一致（非缺陷），守恒恒等式按「放大链精确式+ funding 单调+ 总量=分布和」三式分层锁定 | `_assert_conservation_ref` |
| R-3 | 放大公式作用口径：杠杆放大作用于 bar 收盘估值区间差（非含滑点成交口径 pnl）；lev=1 时终值恒等式退化为 spec 原式 final=init+Σpnl | 契约注记锁定 | `tests/reference/contract.py` 黄金契约 8 |
| R-4 | verify 分段切片语义：`df.index <= end日期` 切至当日 00:00（日期粒度），与全量回测（末根 20:00）差尾部同日 bar——**同窗对齐（三入口传相同 data_start/end）后全等成立**（G-1 裁决：豁免不成立，口径修正成立） | `core/data/mtf_loader.py:69-71`；`test_engine_three_entries_identical` | verify/batch 的 data_ranges 用户须知：end 日期当日晚间 bar 不含 | 已锁定 |
| R-5 | 四入口参数面不对齐：BacktestRequest 有 symbol/timeframe，CompareRequest 无；dna 直传不传 symbol/timeframe 即走 legacy（D-5） | schemas.py 217-274 | API 契约层修复属生产代码变更，登记跟进 | 已锁定 |
| R-6 | batch 入口 = ReplayRunner 重放语义（DecisionPipeline bar 级订单流+VirtualAccount），与引擎三入口（信号回测语义）**设计性不同构**——「四入口全等」修正为「三引擎入口全等」（已验证成立）；batch 差异实测：trades 1 vs 3、total_return −1.06% vs +1.41%（同窗同策略） | `core/trading/replay.py` 头注；`test_batch_replay_semantics_locked` | 批量页指标与验证页指标不可直接比较——产品层是否提示用户为跟进项 | 已锁定（差异锁定断言） |

## 疑点跟踪（未决）

- （组 0-3 实施过程持续追加）
