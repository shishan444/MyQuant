"""执行契约（业务事实注记）——参照实现的复刻依据。

契约 = 被测实现的业务规则（公共事实），非共享代码。参照实现以纯 pandas/numpy
逐笔回测独立复刻同一契约，计算路径异构（被测=vectorbt/numba 净值法）。

契约来源锚点（2026-09-16 探针实验 + 源码检视）：
- core/backtest/engine.py:434-440  信号 shift(1) 防前视
- core/backtest/engine.py:255-395  order_func_nb 动作优先级
- core/backtest/engine.py:158-217  funding / 杠杆放大后处理
- core/scoring/metrics.py          指标公式
- 探针实验（25/16 根手工价格序列，数值全部人工推导核对）

## 黄金契约（探针实测数字锁定）

1. 成交价：entry=close[i]*(1+slip)、exit=close[i]*(1-slip)；short 方向镜像
   （开空卖出 close*(1-slip)、回补 close*(1+slip)）。
2. SL/TP 判定价 entry_price 记录无滑点 close；long: SL 触于 low<=ep*(1-sl)、
   TP 触于 high>=ep*(1+tp)；short 镜像；SL 先于 TP。
   触发后仍按契约 1 的 close±slip 成交（不是 SL 价）。
   [探针: ep=106.053, sl_level=103.88, bar10 low=102.5 触发, xp=104*0.9995=103.948]
3. 股数：shares = value_now*size / (price_slip*(1+fee))，fee 按含滑点名义额。
   [探针: 50000/(106.053*1.001)=470.991391]
4. equity[i] = cash[i] + shares*close[i]（逐 bar 无滑点 close 重估）。
   [探针: bar6 equity=50000+470.991391*106=99925.09]
5. 每根 bar 至多一笔 order，优先级：已清算强平 > 清算检查 > SL > TP >
   exit 信号 > entry 信号 > reduce > add。
6. 清算（vbt 内）：lev>1 持仓时 notional=init_cash*L 选 mmr 档，
   maintenance=init_cash*(1-(1-mmr)/L)，value_now<maintenance 强平（带 fee/slip）。
7. 期末 open trade 以最后 bar close 无滑点无 fee 收尾，PnL 计入。
   [探针: LEV3 场景 xp=94.0, exit_fees=0.0]
8. 杠杆后处理：vbt 基线曲线按 trade 区间 [entry,exit] 放大
   result=base+L*(eq-entry_eq)，区间间 flat=leveraged_base；
   funding=Σ pv[i-1]*0.001*(hours_per_bar/8)*((L-1)/L)（pv=放大后持仓值，
   持仓区间每 bar 扣，从 i=1 起）；liquidated 标志在放大曲线上检测。
9. 指标（metrics.py 公开公式）：total_return=eq[-1]/eq[0]-1；
   annual=(1+tr)^(1/max(years,0.01))-1, years=n/bars_per_year；
   max_dd=min((eq-cummax)/cummax)；sharpe 优先逐笔
   (mean/std_ddof1)*sqrt(trades/max(years,0.01))，trade_returns=pnl/init_cash，
   单笔/不足5笔时 fallback 链（单笔=0.0）；win_rate=(pnl>0)/count。

## bars_per_year（公开事实： timeframe→年 bar 数，4h=365*6 等）
bars_per_year 与 mmr 档位为交易所公开事实，参照实现内联（非共享被测常量）。
"""


'''

## 已知行为偏差（实现缺陷候选——登记独立跟进，本容器不改生产代码）

- D-1 清算盯市晚一根：vbt c.value_now 为前一根收盘估值，清算检查
  value_now<maintenance 因此晚一根触发。证据：liquidation_edge lev5 seed11
  trade0 —— bar(08:00) 当根估值 80051 < 80200 应触发未触发，次根
  (79843) 才触发强平。参照实现已按当前行为锁定 + 哨兵测试
  (test_cross_validation.py::test_liquidation_timing_sentinel)。
- D-2 负权益 annual_return 为复数：(1+total_return)**(1/years) 对负底数
  在 Python 返回 complex（权益穿零场景，liq-edge 高杠杆）。比对器排除该
  字段于负权益 case，登记独立跟进。
'''
