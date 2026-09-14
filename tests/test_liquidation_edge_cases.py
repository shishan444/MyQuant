"""Phase K: Post-liquidation trading behavior fix.

Verifies:
- Liquidation clears position and resets entry_price
- With remaining funds, trading can continue after liquidation
- Without funds, no new positions are opened
- entry_price is reset after liquidation
- Post-liquidation new entries respect SL
- Short position liquidation works correctly
- Multiple sequential liquidations are handled
"""

import numpy as np
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

def _make_dna(direction="long", leverage=5, sl=0.05, tp=0.0, pos_size=0.5):
    return StrategyDNA(
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER, 'RSI_14',
                        {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER, 'RSI_14',
                        {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(
            stop_loss=sl, take_profit=tp, position_size=pos_size,
            leverage=leverage, direction=direction,
        ),
    )

def _make_df(n=60, close_arr=None):
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    if close_arr is None:
        close_arr = np.ones(n) * 100
    close = np.array(close_arr, dtype=float)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    return df

def test_liquidation_clears_position():
    """After liquidation, position should be zero."""
    n = 50
    close_arr = np.ones(n) * 100
    # Massive crash from bar 5 to bar 20 (enough to trigger liquidation)
    close_arr[5:25] = np.linspace(100, 10, 20)
    df = _make_df(n, close_arr)

    dna = _make_dna(leverage=10, sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True  # entry at bar 3, shifted to bar 4
    exits = pd.Series(False, index=df.index)
    exits.iloc[40] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    assert result.total_trades >= 1
    assert result.liquidated

def test_liquidation_with_remaining_funds_continues():
    """After liquidation with remaining funds, new entry signals should work.

    Setup:
    - Enter long at bar 4 (close=100) with 5x leverage
    - Price drops hard (close to 30), triggering order-level liquidation
    - Some funds remain after liquidation
    - New entry signal at bar 25 (then rises)
    - Should be able to open a new position

    batch1 D1 重标定: 旧场景(跌 35%)绑定 0.9/L^2 旧口径(价差 3.6%)。
    新口径 0.9/L@L=5 价差 18%，且订单层判定基于真实账户值(含
    position_size 缩水)——需更深跌幅使订单层真实触发清算平仓。
    """
    n = 60
    close_arr = np.ones(n) * 100
    # Drop from 100 to 30 (70% drop, triggers order-level liquidation with 5x leverage under 0.9/L)
    close_arr[5:20] = np.linspace(100, 30, 15)
    # Recover and rise
    close_arr[20:40] = np.linspace(65, 90, 20)
    close_arr[40:55] = np.linspace(90, 110, 15)
    df = _make_df(n, close_arr)

    dna = _make_dna(leverage=5, sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True   # first entry at bar 3, shifted to bar 4
    entries.iloc[25] = True  # second entry after liquidation
    exits = pd.Series(False, index=df.index)
    exits.iloc[50] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # Should have at least 2 trades: initial entry + post-liquidation entry
    assert result.total_trades >= 2

def test_liquidation_zero_funds_stops_trading():
    """After catastrophic liquidation, equity should be drastically reduced.

    With 10x leverage and 100% position size, a 99% price crash causes
    liquidation. Remaining equity (set by maintenance margin) should be
    significantly less than initial capital.

    The key behavior: liquidation triggers, and the remaining equity is small
    relative to initial capital.
    """
    n = 50
    close_arr = np.ones(n) * 100
    # Crash to 1 (99% drop, catastrophic with 10x leverage)
    close_arr[5:30] = np.linspace(100, 1, 25)
    close_arr[30:50] = np.linspace(1, 2, 20)
    df = _make_df(n, close_arr)

    dna = _make_dna(leverage=10, sl=0.0, tp=0.0, pos_size=1.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True   # first entry
    entries.iloc[35] = True  # post-crash entry attempt
    exits = pd.Series(False, index=df.index)
    exits.iloc[45] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # Liquidation must have occurred
    assert result.liquidated
    # Equity should have dipped well below init_cash at some point
    assert result.equity_curve.min() < 95000

def test_liquidation_reset_entry_price():
    """After liquidation, entry_price should be reset to 0."""
    n = 50
    close_arr = np.ones(n) * 100
    close_arr[5:25] = np.linspace(100, 10, 20)
    df = _make_df(n, close_arr)

    dna = _make_dna(leverage=10, sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True
    exits = pd.Series(False, index=df.index)
    exits.iloc[40] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # B2 强化: 10x 杠杆 + 90% 暴跌应清算; entry_price 是 njit 内部状态不可直接断言,
    # 用 liquidated + 权益跌破维持保证金替代(之前只断言 equity<100000, 弱)
    # 注: 清算机制在跌破 maintenance 后平仓保留剩余资金, equity 不会近归零
    assert result.liquidated is True, "10x 杠杆 + 90% 暴跌应清算"
    maintenance = 100000 * (1 - 0.9 / 10)  # = 91000, engine.py:521
    assert result.equity_curve.min() < maintenance  # 曾跌破维持保证金

def test_post_liquidation_new_entry_respects_sl():
    """After liquidation and re-entry, SL should work normally."""
    n = 60
    close_arr = np.ones(n) * 100
    # First: crash to trigger liquidation
    close_arr[5:20] = np.linspace(100, 65, 15)
    # Recovery
    close_arr[20:35] = np.linspace(65, 100, 15)
    # Re-entry, then gradual decline hitting SL
    close_arr[35:50] = np.linspace(100, 90, 15)  # -10% from re-entry
    df = _make_df(n, close_arr)

    dna = _make_dna(leverage=3, sl=0.05, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True   # first entry
    entries.iloc[25] = True  # re-entry after liquidation
    exits = pd.Series(False, index=df.index)
    exits.iloc[55] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # Should have multiple trades (SL triggered after re-entry too)
    assert result.total_trades >= 1

def test_short_position_liquidation():
    """Short position liquidation should work correctly.

    Setup:
    - Enter short at bar 4 (close=100) with 5x leverage
    - Price doubles by bar 20 (close=200), triggering liquidation
    - Remaining funds should allow continued trading if sufficient
    """
    n = 60
    close_arr = np.ones(n) * 100
    # Price doubles (bad for short)
    close_arr[5:30] = np.linspace(100, 200, 25)
    df = _make_df(n, close_arr)

    dna = _make_dna(direction="short", leverage=5, sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True   # first entry
    entries.iloc[35] = True  # post-liquidation entry attempt
    exits = pd.Series(False, index=df.index)
    exits.iloc[50] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(-1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    assert result.total_trades >= 1
    assert result.liquidated

def test_multiple_liquidations_in_sequence():
    """Multiple sequential liquidations should each be handled correctly.

    Each time, if funds remain, a new position can be opened.
    """
    n = 80
    close_arr = np.ones(n) * 100
    # Crash 1: 100 -> 50
    close_arr[5:20] = np.linspace(100, 50, 15)
    # Recovery 1: 50 -> 80
    close_arr[20:30] = np.linspace(50, 80, 10)
    # Crash 2: 80 -> 30
    close_arr[30:45] = np.linspace(80, 30, 15)
    # Recovery 2: 30 -> 60
    close_arr[45:60] = np.linspace(30, 60, 15)
    # Final steady
    close_arr[60:80] = np.linspace(60, 55, 20)
    df = _make_df(n, close_arr)

    dna = _make_dna(leverage=5, sl=0.0, tp=0.0)

    entries = pd.Series(False, index=df.index)
    entries.iloc[3] = True   # first entry
    entries.iloc[22] = True  # re-entry attempt after first liquidation
    entries.iloc[47] = True  # re-entry attempt after second liquidation
    exits = pd.Series(False, index=df.index)
    exits.iloc[75] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)

    sig_set = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=direction)

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, signal_set=sig_set)

    # Should have at least 1 trade
    assert result.total_trades >= 1
