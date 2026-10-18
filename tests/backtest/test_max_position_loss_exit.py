"""持仓金额防线执行语义——引擎行为（add-position-sizing-params 2.2，
族 10a/10b/10c/10d）。

合成数据+手工 SignalSet 精确控 bar（信号统一 shift(1)——信号 bar b 的
执行 bar 为 b+1）。判定锚=portfolio.trades 逐笔 Entry/Exit bar 与出场价。

成交机制说明（与 time_exit/max_hold 同形制的直接推论）：防线判定用
当根已闭合 bar 的收盘价、出场单当根 close 成交（扣滑点）——与 SL/TP/
time_exit/max_hold/信号出场全部同款 order_nb 成交机制（U6c 探针结论
同源；纸盘侧「次根开盘」执行与引擎的跨环境一致性由受控等价层平价 bar
构造承载，见 tests/helpers/data_factory.make_flat_ohlcv docstring）。
设计卡「vbt 次根成交=次根开盘出场」措辞的偏差对质已由 team-lead 裁决
（2026-10-18）：采纳同形制实现——「次根开盘」是纸盘 pending 机制的
出场形制措辞外溢，引擎侧全部出场机制均为当根 close 扣滑点；两环境
出场价形制差属既有环境形制差，跨环境等价归组 6 受控等价（平价 bar）
承载，与 Q-1 预排口径同一处理族。

等价锚原理：恒定价格序列下浮亏=0，loss≡funding_acc（旁路累计）；
触发 bar 的手算累计 F(i)=Σ 每根增量（=|仓位|×close×lev×rate——与
事后层 _apply_funding_costs 滞后基数同式）双向夹逼出「旁路终值==
事后层对应持仓段累计」。
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine, _funding_cost_rate
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)
from core.strategy.executor import SignalSet

N = 60


def _df(prices=None) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=N, freq="4h", tz="UTC",
                        name="timestamp")
    p = prices if prices is not None else np.full(N, 100.0)
    p = np.asarray(p, dtype=float)
    return pd.DataFrame({
        "open": p, "high": p * 1.001, "low": p * 0.999, "close": p,
        "volume": np.full(N, 100.0), "trades": 10,
    }, index=idx)


def _sig(entries_bars=(2,)):
    z = pd.Series(0.0, index=_df().index)
    e, x, r, a = z.copy(), z.copy(), z.copy(), z.copy()
    for b in entries_bars:
        e.iloc[b] = 1.0
    return SignalSet(entries=e, exits=x, adds=a, reduces=r)


def _gene():
    return SignalGene(
        indicator="RSI", params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
        condition={"type": "lt", "threshold": 999})


def _dna(mpl=None, direction="long", leverage=3, size=0.5,
         stop_loss=None, **risk_kw):
    rg = RiskGenes(direction=direction, stop_loss=stop_loss,
                   take_profit=None, position_size=size,
                   leverage=leverage, **risk_kw)
    if mpl is not None:
        rg.max_position_loss = mpl
    return StrategyDNA(
        signal_genes=[_gene()], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=rg)


def _run(dna, df=None, sig=None):
    df = df if df is not None else _df()
    if sig is None:
        sig = _sig()   # 手工信号精确控 bar（禁 RSI 重算漂移）
    result = BacktestEngine().run(
        dna, df, dfs_by_timeframe={"4h": df}, signal_set=sig)
    return df, result


def _bars_of(df, row) -> tuple[int, int]:
    return (df.index.get_loc(pd.Timestamp(row["Entry Timestamp"])),
            df.index.get_loc(pd.Timestamp(row["Exit Timestamp"])))


def _exit_price(row) -> float:
    col = "Exit Price" if "Exit Price" in row.index else "Avg Exit Price"
    return float(row[col])


class TestEquivalenceAnchor:
    """族 10a 等价锚：出场时点 funding_acc 终值==事后层对应段累计。

    恒定价格 100、lev=3、4h——rate=1/3000，入场执行 bar i₀=3、仓位
    恒定 → 每根累计增量 incr=|仓位|×100×3×rate（事后层独立算的
    total_funding_cost 与之逐位同式，见各用例内对拍）。
    """

    def _baseline(self):
        """无防线基线：仓位观测值+解析每根增量。"""
        df, res = _run(_dna())
        row = res.trades_df.iloc[0]
        pos = abs(float(row["Size"]))
        rate = _funding_cost_rate(3, "4h")
        incr = pos * 100.0 * 3.0 * rate
        return pos, incr, res.total_funding_cost

    def test_exit_bar_matches_hand_derived_accumulation(self):
        """L=120：F(5)=incr×2<120≤F(6)=incr×3 → 恰 bar 6 触发；且出场
        时点事后层累计 total_funding_cost≈F(6)（两条独立路径同式对拍
        ——旁路判定读值与事后层扣费在出场 bar 收敛同值）。"""
        pos, incr, _ = self._baseline()
        df, res = _run(_dna(mpl=120.0))
        entry_b, exit_b = _bars_of(df, res.trades_df.iloc[0])
        assert entry_b == 3
        assert incr * 2 < 120.0 <= incr * 3, "构造前提：F(5)<L≤F(6)"
        assert exit_b == 6, f"应 bar 6 触发，实际 {exit_b}"
        # 事后层对 [3,6] 段的累计==F(6)（=出场时点 funding_acc 终值）
        assert abs(res.total_funding_cost - incr * 3) <= incr * 3 * 1e-9, \
            f"事后层累计 {res.total_funding_cost} != 手算 F(6) {incr * 3}"

    def test_threshold_boundary_inclusive_squeeze(self):
        """含等语义+增量精确性夹逼：L=F(6)−ε → bar 6 触发（≥ 含等）；
        L=F(6)+ε → bar 7 触发（差一根）。夹逼锁定旁路每根增量与事后层
        精确一致（浮点容差内）。"""
        pos, incr, _ = self._baseline()
        f6 = incr * 3
        eps = f6 * 1e-9
        df_lo, res_lo = _run(_dna(mpl=f6 - eps))
        _, exit_lo = _bars_of(df_lo, res_lo.trades_df.iloc[0])
        assert exit_lo == 6, "恰达线（含等）应在 bar 6 触发"
        df_hi, res_hi = _run(_dna(mpl=f6 + eps))
        _, exit_hi = _bars_of(df_hi, res_hi.trades_df.iloc[0])
        assert exit_hi == 7, "微超线应顺延 bar 7 触发"

    def test_declaration_zero_side_effect_on_funding_total(self):
        """状态隔离约束（可核验）：声明防线（不触发档）不改变事后层
        资金费——total_funding_cost 与不声明完全相等（funding_acc 旁路
        纯读零副作用，对外可见资金费事实仍只有事后层一处）。"""
        _, _, base_total = self._baseline()
        df, res = _run(_dna(mpl=1e9))  # 永不触发档
        assert res.total_funding_cost == base_total


class TestRegressionAnchor:
    """族 10b 回归锚：不声明逐字节不变（v8 字节锚=固定样本 metrics 与
    改造前逐位一致的直接承载，见 tests/strategy/test_v8_byte_anchor.py；
    本类补行为对照：缺省 None 与显式无键输出全同）。"""

    def test_undeclared_none_vs_no_key_equivalence(self):
        df = _df()
        sig = _sig()
        _, ra = _run(_dna(), df=df, sig=sig)   # 缺省 None
        _, rb = _run(_dna(), df=df, sig=sig)
        rb2 = StrategyDNA(                      # 显式构造无新键 DNA
            signal_genes=[_gene()], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(direction="long", stop_loss=None,
                                 take_profit=None, position_size=0.5,
                                 leverage=3))
        _, rc = _run(rb2, df=df, sig=sig)
        pd.testing.assert_series_equal(ra.equity_curve, rc.equity_curve)
        assert ra.total_trades == rc.total_trades
        assert ra.metrics_dict == rc.metrics_dict
        assert ra.total_funding_cost == rc.total_funding_cost


class TestMechanismAnchors:
    """族 10c/10d 机制锚：位次/成交形制/空头双向/同根归止损/首判短路。"""

    def test_sl_same_bar_precedence(self):
        """与止损同根双触发归止损：bar 8 探穿 SL 线且防线同根达线——
        出场 bar/价与「无防线纯 SL」场景一致（SL 段在前先 return；双
        命中恰一笔 Closed 全平——U6c 可观测面同款）。"""
        p = np.full(N, 100.0)
        p[8] = 96.5   # low≈96.4<SL 线 97；close 浮亏 3.5×pos≈1750
        df = _df(p)
        both = _dna(mpl=1000.0, leverage=1, stop_loss=0.03)
        only_sl = _dna(leverage=1, stop_loss=0.03)
        _, r_both = _run(both, df=df)
        _, r_sl = _run(only_sl, df=df)
        assert r_both.total_trades == r_sl.total_trades
        b_both = _bars_of(df, r_both.trades_df.iloc[0])
        b_sl = _bars_of(df, r_sl.trades_df.iloc[0])
        assert b_both == b_sl == (3, 8), "同根双触发归止损（出场不变）"
        assert abs(_exit_price(r_both.trades_df.iloc[0])
                   - _exit_price(r_sl.trades_df.iloc[0])) < 1e-12

    def test_guard_fires_before_time_exit(self):
        """位次：防线先于 time_exit——防线 bar 5 触发时出场 bar 5（若
        time_exit 优先会是 bar 6=次日 0:00 覆盖 bar）；防线不触发档出
        场 bar 6（time_exit 机制在场证明前提有效）。"""
        p = np.full(N, 100.0)
        p[5] = 99.7   # 浮亏≈500×0.3=150（lev=1 无资金费分量）
        df = _df(p)
        guarded = _dna(mpl=100.0, leverage=1, time_exit_utc=0)
        df_g, r_g = _run(guarded, df=df)
        _, exit_g = _bars_of(df_g, r_g.trades_df.iloc[0])
        assert exit_g == 5, f"防线应先于 time_exit 在 bar 5 出场，实际 {exit_g}"
        control = _dna(mpl=1e9, leverage=1, time_exit_utc=0)
        df_c, r_c = _run(control, df=df)
        _, exit_c = _bars_of(df_c, r_c.trades_df.iloc[0])
        assert exit_c == 6, "time_exit 机制在场（次日 0:00 覆盖 bar 6）"

    def test_exit_price_close_minus_slippage_form(self):
        """成交形制：出场价=触发 bar close 扣滑点（与 SL/time_exit/
        信号出场同一 order_nb 成交机制；「次根开盘」的跨环境承载见
        模块 docstring）。"""
        p = np.full(N, 100.0)
        p[7] = 99.5   # 浮亏≈500×0.5=250≥L=200
        df = _df(p)
        df_g, r = _run(_dna(mpl=200.0, leverage=1), df=df)
        _, exit_b = _bars_of(df_g, r.trades_df.iloc[0])
        assert exit_b == 7
        expected = 99.5 * (1 - 0.0005)
        assert abs(_exit_price(r.trades_df.iloc[0]) - expected) < 1e-9

    def test_long_pure_price_loss_hand_calc(self):
        """多头纯价格浮亏（lev=1 费率 0——累计短路的对照路径）：bar 7
        close=99.5 → loss=|仓位|×0.5≥200 触发；bar 6 未达线持有。"""
        p = np.full(N, 100.0)
        p[7] = 99.5
        df = _df(p)
        df_g, res = _run(_dna(mpl=200.0, leverage=1), df=df)
        row = res.trades_df.iloc[0]
        entry_b, exit_b = _bars_of(df_g, row)
        pos = abs(float(row["Size"]))
        loss6 = pos * (100.0 - 99.7)   # bar 6 close=99.7
        assert exit_b == 7
        assert loss6 < 200.0 <= pos * 0.5, "构造前提：bar 6 未达线"

    def test_short_direction_signed_formula(self):
        """空头双向（公式带符号无方向分支）：空头 pos<0、价格上漲
        (close−entry)>0 → −(负×正)=正=浮亏态触发；价平段不触发。"""
        p = np.full(N, 100.0)
        p[6] = 100.2   # loss≈pos×0.2≈100<200 未达
        p[8] = 100.5   # loss≈pos×0.5≈250≥200 触发
        df = _df(p)
        df_g, res = _run(_dna(mpl=200.0, direction="short", leverage=1),
                         df=df)
        row = res.trades_df.iloc[0]
        entry_b, exit_b = _bars_of(df_g, row)
        pos = abs(float(row["Size"]))
        assert row["Direction"] == "Short"
        assert pos * 0.2 < 200.0 <= pos * 0.5, "构造前提：bar 6 未达线"
        assert exit_b == 8, f"空头浮亏达线应 bar 8 触发，实际 {exit_b}"

    def test_sentinel_zero_overhead_circuit(self):
        """首判短路存量零开销：未声明→装配物化 0.0 禁用哨兵（判定段
        首判即假、funding_acc 恒零）；行为面=不声明对照（本模块回归锚
        与 v8 字节锚承载）。"""
        from tests.helpers.data_factory import make_enhanced_df
        from core.backtest.engine import _per_dna_risk_params
        df = make_enhanced_df(30)
        assert _per_dna_risk_params(RiskGenes(), df)[10] == 0.0, \
            "None 物化 0.0 禁用哨兵"
        assert _per_dna_risk_params(
            RiskGenes(max_position_loss=500.0), df)[10] == 500.0

    def test_batch_single_source(self):
        """双入口同源：批量列 0（声明防线）与单路径同 DNA 出场 bar 一致；
        列 1（无声明）持仓到末根（防装配漏接的静默分叉——ENG-8 形制）。"""
        p = np.full(N, 100.0)
        p[7] = 99.5
        df = _df(p)
        sig = _sig()
        results = BacktestEngine().batch_run(
            [_dna(mpl=200.0, leverage=1), _dna(leverage=1)],
            enhanced_df=df, dfs_by_timeframe={"4h": df},
            signal_sets=[sig, sig])
        assert len(results) == 2
        b0 = int(results[0].trades_df["Exit Timestamp"].iloc[0])
        b1 = int(results[1].trades_df["Exit Timestamp"].iloc[0])
        assert b0 == 7, "批量路径防线生效（bar 7 触发）"
        assert b1 == N - 1, "批量路径无声明持仓至末根"
        df_s, r_s = _run(_dna(mpl=200.0, leverage=1), df=df)
        _, exit_s = _bars_of(df_s, r_s.trades_df.iloc[0])
        assert b0 == exit_s, "批量与单路径同源（出场 bar 一致）"
