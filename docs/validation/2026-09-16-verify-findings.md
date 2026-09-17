# verify-myquant-capability-integrity · 发现登记清单

容器期累积工件（两级处置：口径分歧→本容器裁决锁定；实现缺陷→登记证据独立跟进，不修生产代码）。

## 实现缺陷候选（独立 fix 分支跟进）

| # | 发现 | 证据锚点 | 影响面 | 状态 |
|---|---|---|---|---|
| D-1 | 清算盯市晚一根：vbt `c.value_now` 在 order 执行时刻为前一根收盘估值，`value_now < maintenance` 检查因此晚一根触发强平 | `tests/reference/contract.py` D-1 注记；哨兵 `test_cross_validation.py::test_liquidation_timing_sentinel`；实证 liquidation_edge lev5 seed11 trade0（当根 80051<80200 未触发，次根 79843 触发） | 高杠杆边界场景多担一根清算风险（真实损失放大） | 已锁定行为+哨兵；待独立 fix |
| D-2 | 负权益 annual_return 为复数：`(1+total_return)**(1/years)` 负底数浮点幂 Python 返回 complex（numpy 路径为 nan） | `core/scoring/metrics.py:70`；实证 liq-edge-lev5（engine 复数 0.76+4.44j vs ref nan）；比对器于穿零 case 排除该字段 | 权益穿零场景指标无意义值外泄（API 序列化层若 float() 将崩） | 比对排除+登记；待独立 fix |
| D-3 | 负权益 max_drawdown 突破值域：`(eq−cummax)/cummax` 于负 cummax 区发散，产出 <−1（实测 −2.43）；两实现同值同错（恒等式族抓「两实现同错」盲区的首例） | `core/scoring/metrics.py:77`；实证 liq-edge-lev5 双侧同值 −2.42919105 | 穿零场景风险指标失义；评分/展示层若消费此值产生误导 | 值域断言穿零降级+登记；待独立 fix |

## 口径裁决记录（本容器锁定）

| # | 事项 | 裁决 | 锚点 |
|---|---|---|---|
| R-1 | funding 的持仓值口径：被测 `position_values = raw_equity − cash` 对空头持仓为负 → funding 反向增加权益 | 参照按「借入成本」\|pv\| 正确语义实现；空头+杠杆场景比对若分歧如实报 DiffReport（本批五场景未触发该组合，登记待裁决） | `tests/reference/reference_backtest.py` 模块注记 |
| R-2 | funding 逐 bar 扣减不传播：`adjusted[i] -= cost` 逐点独立，不滚动进后续 bar 权益 | 两实现口径一致（非缺陷），守恒恒等式按「放大链精确式+ funding 单调+ 总量=分布和」三式分层锁定 | `_assert_conservation_ref` |
| R-3 | 放大公式作用口径：杠杆放大作用于 bar 收盘估值区间差（非含滑点成交口径 pnl）；lev=1 时终值恒等式退化为 spec 原式 final=init+Σpnl | 契约注记锁定 | `tests/reference/contract.py` 黄金契约 8 |

## 疑点跟踪（未决）

- （组 0-3 实施过程持续追加）
