"""时间离场执行语义——引擎行为（add-max-hold-exit D3，ENG-1~ENG-10）。

合成数据+手工 SignalSet 精确控 bar（信号统一 shift(1)——信号 bar b 的
执行 bar 为 b+1；i₀=入场执行 bar）。判定锚=portfolio.trades 逐笔
Entry/Exit Timestamp 与出场价（引擎测试面——API 面不披露逐笔明细）。

A-1 口径：max_hold=N 语义=最长持有 N 根 K 线（入场成交 bar 计第 1
根），持有第 N 根 bar 的出场时点无条件平仓——i₀ 至 i₀+N−1 恰 N 根。
"""

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
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
    import numpy as np
    idx = pd.date_range("2024-01-01", periods=N, freq="4h", tz="UTC",
                        name="timestamp")
    p = prices if prices is not None else np.linspace(60000, 54000, N)
    p = np.asarray(p, dtype=float)
    return pd.DataFrame({
        "open": p, "high": p * 1.001, "low": p * 0.999, "close": p,
        "volume": np.full(N, 100.0), "trades": 10,
    }, index=idx)


def _sig(entries_bars, exits_bars=(), reduces_bars=(), adds_bars=()):
    z = pd.Series(0.0, index=_df().index)
    e, x, r, a = z.copy(), z.copy(), z.copy(), z.copy()
    for b in entries_bars:
        e.iloc[b] = 1.0
    for b in exits_bars:
        x.iloc[b] = 1.0
    for b in reduces_bars:
        r.iloc[b] = 1.0
    for b in adds_bars:
        a.iloc[b] = 1.0
    return SignalSet(entries=e, exits=x, adds=a, reduces=r)


def _gene():
    return SignalGene(
        indicator="RSI", params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
        condition={"type": "lt", "threshold": 999})


def _dna(max_hold=None, direction="long", **risk_kw):
    rg = RiskGenes(direction=direction, **risk_kw)
    if max_hold is not None:
        rg.max_hold = max_hold
    return StrategyDNA(
        signal_genes=[_gene()], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=rg)


def _run(dna, df=None, sig=None):
    df = df if df is not None else _df()
    result = BacktestEngine().run(
        dna, df, dfs_by_timeframe={"4h": df}, signal_set=sig)
    return df, result


def _bars(ts_entry, ts_exit):
    """Entry/Exit Timestamp → 执行 bar 序号差（含首尾的持有根数）。"""
    return int((pd.Timestamp(ts_exit) - pd.Timestamp(ts_entry))
               / pd.Timedelta(hours=4)) + 1


def _exit_price_col(trades_df) -> str:
    """vbt records_readable 列名在单笔（Exit Price）与拆分多笔
    （Avg Exit Price）间变化——容错取列。"""
    if "Exit Price" in trades_df.columns:
        return "Exit Price"
    return "Avg Exit Price"


class TestExpiryMatchesHandCalc:
    def test_eng1_expiry_bar_exactly_n_bars(self):
        """ENG-1（change 级必过）：入场 i₀=3、max_hold=6 → 恰在 bar 8
        平仓（3..8 恰 6 根）；出场价=出场 bar close（与信号出场同一
        成交机制——同口径直接证据）。"""
        df, r = _run(_dna(max_hold=6), sig=_sig([2]))
        assert r.total_trades >= 1
        t0 = r.trades_df.iloc[0]
        assert _bars(t0["Entry Timestamp"], t0["Exit Timestamp"]) == 6
        exit_bar = df.index.get_loc(pd.Timestamp(t0["Exit Timestamp"]))
        exit_price = float(t0[_exit_price_col(r.trades_df)])
        # 成交口径=出场 bar close 扣滑点（与信号出场同一 order_nb 成交
        # 机制——时间出场单与 exits 信号出场单参数逐字同款）
        expected = float(df["close"].iloc[exit_bar]) * (1 - 0.0005)
        assert abs(exit_price - expected) < 1e-6

    def test_eng2_no_declaration_holds_longer(self):
        """ENG-2（决定性差异）：同数据同信号去掉 max_hold → 持仓越过
        第 6 根持续至数据末端。"""
        df, r = _run(_dna(), sig=_sig([2]))
        t0 = r.trades_df.iloc[0]
        assert _bars(t0["Entry Timestamp"], t0["Exit Timestamp"]) > 6

    def test_eng3_n1_degenerate_equivalence(self):
        """ENG-3（spec「N=1 退化等价」锚）：N=1 与 N=2 逐笔出场 bar
        完全一致（均在入场次 bar 平仓）且出场价相等。"""
        df, r1 = _run(_dna(max_hold=1), sig=_sig([2]))
        _, r2 = _run(_dna(max_hold=2), sig=_sig([2]))
        t1, t2 = r1.trades_df, r2.trades_df
        assert (t1["Exit Timestamp"].tolist() == t2["Exit Timestamp"].tolist())
        c1, c2 = _exit_price_col(t1), _exit_price_col(t2)
        assert abs(float(t1[c1].iloc[0])
                   - float(t2[c2].iloc[0])) < 1e-9
        # 最早可用出场时点=入场次 bar（i₀=3 → 4），不早于 i₀+1
        exit_bar = df.index.get_loc(pd.Timestamp(t1["Exit Timestamp"].iloc[0]))
        assert exit_bar == 4


class TestSameBarPrecedence:
    """ENG-4/ENG-5：同 bar 双命中时价格触发（SL/TP）优先于到期。"""

    def _tp_df(self):
        import numpy as np
        p = np.full(N, 60000.0)
        p[8] = 66600.0    # 到期候选 bar 冲高（high>TP 线 66000）
        return _df(p)

    def test_eng4_tp_fires_before_expiry(self):
        df = self._tp_df()
        # max_hold=6 到期 bar=8 与 TP 触碰 bar 重合；max_hold=7 到期
        # 在后——两者出场 bar 均为 8（TP 先执行，时间出场不抢先）；
        # max_hold=5 到期 bar=7 先于 TP → bar 7 出场（时间出场路径）
        _, r6 = _run(_dna(max_hold=6, take_profit=0.10, stop_loss=0.0), df=df, sig=_sig([2]))
        _, r7 = _run(_dna(max_hold=7, take_profit=0.10, stop_loss=0.0), df=df, sig=_sig([2]))
        _, r5 = _run(_dna(max_hold=5, take_profit=0.10, stop_loss=0.0), df=df, sig=_sig([2]))
        b6 = df.index.get_loc(pd.Timestamp(r6.trades_df["Exit Timestamp"].iloc[0]))
        b7 = df.index.get_loc(pd.Timestamp(r7.trades_df["Exit Timestamp"].iloc[0]))
        b5 = df.index.get_loc(pd.Timestamp(r5.trades_df["Exit Timestamp"].iloc[0]))
        assert b6 == b7 == 8, "TP 触碰 bar 到期不抢先（价格触发优先）"
        assert b5 == 7, "到期先于 TP 触碰 → 时间出场先执行"

    def test_eng5_sl_fires_before_expiry(self):
        import numpy as np
        p = np.full(N, 60000.0)
        p[8] = 53400.0    # bar 8 探低（low<SL 线 57000）
        df = _df(p)
        _, r6 = _run(_dna(max_hold=6, stop_loss=0.05), df=df, sig=_sig([2]))
        _, r7 = _run(_dna(max_hold=7, stop_loss=0.05), df=df, sig=_sig([2]))
        b6 = df.index.get_loc(pd.Timestamp(r6.trades_df["Exit Timestamp"].iloc[0]))
        b7 = df.index.get_loc(pd.Timestamp(r7.trades_df["Exit Timestamp"].iloc[0]))
        assert b6 == b7 == 8, "SL 触碰 bar 到期不抢先（止损保护优先于到期）"


class TestInteractionSemantics:
    def test_eng6_reduce_does_not_reset_counter(self):
        """ENG-6：中途部分减仓（exit_fraction=0.5，bar 5 执行）后计数
        继续、到 N 平余仓——最终段出场 bar 仍 8（重置则会在 bar 10）。
        vbt 将减仓拆分为共享 Entry 的多段记录——取 Exit 最大段判定。"""
        df, r = _run(
            _dna(max_hold=6, exit_fraction=0.5),
            sig=_sig([2], reduces_bars=[4]))
        assert r.total_trades >= 2, "减仓拆分记录在场"
        last = r.trades_df.sort_values("Exit Timestamp").iloc[-1]
        assert _bars(last["Entry Timestamp"], last["Exit Timestamp"]) == 6

    def test_eng7_reentry_independent_counting(self):
        """ENG-7：出场后再入场从新 bar 起算（i₀₁=3→8、i₀₂=10→15）。"""
        df, r = _run(_dna(max_hold=6), sig=_sig([2, 9]))
        assert r.total_trades >= 2
        holds = [_bars(t["Entry Timestamp"], t["Exit Timestamp"])
                 for _, t in r.trades_df.iterrows()]
        assert holds == [6, 6]

    def test_eng7_sl_exit_reentry_independent_counting(self):
        """ENG-7 SL 路径补（tester）：spec「再入场独立计数」Scenario 字面
        是「SL 出场后同策略再次入场→计数从新入场 bar 重新起算」——既有
        ENG-7 第一笔经 max_hold 到期出场，SL 出场路径（entry_bar 不清零
        的残留路径——SL/TP 段只清 entry_price/peak_price）无字面覆盖。

        编排：i₀₁=3，bar 5 探低触发 SL（持有 3 根<6——第一笔非到期
        出场）；i₀₂=7 再入场，第二笔恰持有 6 根（bar 12 到期）。若
        entry_bar/计数未重置（残留第一笔 bar 3），第二笔会在 bar 8
        提前平仓（持有 2 根）——断言值恰好区分两形态。
        """
        import numpy as np
        p = np.full(N, 60000.0)
        p[5] = 53400.0    # bar 5 探低（low≈53347<SL 线 57000）
        df, r = _run(_dna(max_hold=6, stop_loss=0.05), df=_df(p),
                     sig=_sig([2, 6]))
        assert r.total_trades == 2
        t1, t2 = (r.trades_df.sort_values("Entry Timestamp")
                  .iloc[0], r.trades_df.sort_values("Entry Timestamp")
                  .iloc[1])
        b1_exit = df.index.get_loc(pd.Timestamp(t1["Exit Timestamp"]))
        assert b1_exit == 5, "第一笔经 SL 出场（非到期）"
        holds = [_bars(t["Entry Timestamp"], t["Exit Timestamp"])
                 for _, t in r.trades_df.iterrows()]
        assert holds == [3, 6], "第二笔从新入场 bar 起算恰持有 6 根"
        b2_exit = df.index.get_loc(pd.Timestamp(t2["Exit Timestamp"]))
        assert b2_exit == 12, "第二笔 bar 12 到期平仓（残留 bar 3 则 bar 8）"


class TestBatchAndRegression:
    def test_eng8_batch_single_source(self):
        """ENG-8（change 级必过）：批量路径 max_hold 列真实生效——批量
        列 0（max_hold=6）与单路径同 DNA 行为一致；列 1（无声明）持仓
        更久（防 CARD-04 静默分叉：批量漏装即该列按 0 执行）。
        批量 trades_df 时间戳列为 bar 序号形制（iloc 展平）——直接以
        序号断言。"""
        df = _df()
        sig = _sig([2])
        results = BacktestEngine().batch_run(
            [_dna(max_hold=6), _dna()], enhanced_df=df,
            dfs_by_timeframe={"4h": df}, signal_sets=[sig, sig])
        assert len(results) == 2
        b0 = results[0].trades_df
        b1 = results[1].trades_df
        hold0 = int(b0["Exit Timestamp"].iloc[0]) - int(b0["Entry Timestamp"].iloc[0]) + 1
        hold1 = int(b1["Exit Timestamp"].iloc[0]) - int(b1["Entry Timestamp"].iloc[0]) + 1
        assert hold0 == 6, "批量路径 max_hold 生效"
        assert hold1 > 6, "批量路径无声明对照持仓更久"
        # 与单路径一致（单/批量同源；单路径时间戳形制换算核对）
        _, single = _run(_dna(max_hold=6), df=df, sig=sig)
        s0 = single.trades_df
        hold_s = _bars(s0["Entry Timestamp"].iloc[0],
                       s0["Exit Timestamp"].iloc[0])
        assert hold0 == hold_s

    def test_eng9_no_declaration_byte_equivalence(self):
        """ENG-9（change 级必过）：缺省 None 与显式无键 DNA 输出全同
        （None 装配物化 0=禁用精确等价）；「与改造前逐字节一致」由
        既有引擎测试零改写绿承担（全量回归锚）。"""
        df = _df()
        sig = _sig([2])
        dna_a = _dna()                       # 缺省 None
        dna_b = StrategyDNA(                 # 显式构造无 max_hold 键
            signal_genes=[_gene()], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        _, ra = _run(dna_a, df=df, sig=sig)
        _, rb = _run(dna_b, df=df, sig=sig)
        pd.testing.assert_series_equal(ra.equity_curve, rb.equity_curve)
        assert ra.total_trades == rb.total_trades
        assert abs(float(ra.total_return) - float(rb.total_return)) < 1e-12

    def test_eng10_short_direction_composition(self):
        """ENG-10：与 change A 组合——short 方向上时间出场同样生效
        （单调跌段 short 入场持有恰 N 根离场）。"""
        df, r = _run(_dna(max_hold=6, direction="short"), sig=_sig([2]))
        assert r.total_trades >= 1
        t0 = r.trades_df.iloc[0]
        assert t0["Direction"] == "Short"
        assert _bars(t0["Entry Timestamp"], t0["Exit Timestamp"]) == 6
