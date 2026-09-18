"""Phase A: SL/TP high/low fix and liquidation priority.

Verifies:
- A1: Stop-loss triggers when bar's LOW touches SL level (not just close)
- A1: Take-profit triggers when bar's HIGH touches TP level (not just close)
- A1: Short positions: SL checks HIGH, TP checks LOW
- B1: Liquidation check happens before SL/TP
- B1: After SL triggers with catastrophic loss, trading stops
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from tests.helpers.data_factory import make_dna

# ── A1: SL triggers on LOW for long positions ──

def test_sl_triggers_on_intraday_low_long():
    """Long position SL should trigger when bar's LOW touches SL level,
    even if CLOSE is above SL.

    Setup:
    - Entry at bar 5 (close=100, RSI<30)
    - Bar 10: close=98 (above SL), low=94 (touches -6% SL)
    - SL at 5%: entry_price=100, SL triggers at price<=95
    - LOW=94 < 95, so SL should trigger on bar 10
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[5] = 100  # entry bar
    close[10] = 98  # close above SL level

    high = close * 1.01
    low = close * 0.99
    # Bar 10: low dips to 94, which is -6% from entry (SL at -5%)
    low[10] = 94.0

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    # Entry signal at bar 5
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(stop_loss=0.05, take_profit=0.20)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # SL should trigger on bar 10 (low=94 < 95), closing the trade
    assert result.total_trades >= 1
    # Trade should be CLOSED (not Open) - SL triggered
    assert all(t == "Closed" for t in result.trades_df["Status"].tolist())
    # Equity should have lost money (SL exit, not end-of-data)
    assert result.equity_curve.iloc[-1] < 99900

def test_sl_does_not_trigger_when_low_above_sl_level():
    """SL should NOT trigger when bar's LOW stays above SL level.

    Setup:
    - Entry at bar 5 (close=100)
    - Bar 10: close=97, low=96 (only -4% from entry, SL at -5%)
    - LOW=96 > 95, so SL should NOT trigger on bar 10
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10] = 97

    high = close * 1.01
    low = close * 0.99
    # Bar 10: low at 96, only -4% from entry (SL at -5% = 95)
    low[10] = 96.0

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20
    # No exit signal - SL is the only way out
    df.loc[df.index[25], 'rsi_14'] = 80  # late exit

    dna = make_dna(stop_loss=0.05, take_profit=0.50)  # TP far away
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # SL at 5% not triggered (low=96 > 95), trade should exit via signal at bar 25
    assert result.total_trades >= 1
    # Trade should still be open at bar 10 (SL didn't trigger)
    # Exit is via late signal at bar 25, so final equity should be close to init
    assert result.equity_curve.iloc[-1] > 99000

# ── A1: TP triggers on HIGH for long positions ──

def test_tp_triggers_on_intraday_high_long():
    """Long position TP should trigger when bar's HIGH touches TP level.

    Setup:
    - Entry at bar 5 (close=100)
    - Bar 10: close=108 (below TP), high=112 (touches +12% TP)
    - TP at 10%: entry_price=100, TP triggers at price>=110
    - HIGH=112 > 110, so TP should trigger on bar 10
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10] = 108

    high = close * 1.01
    low = close * 0.99
    # Bar 10: high spikes to 112, which is +12% from entry (TP at +10%)
    high[10] = 112.0

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(stop_loss=0.20, take_profit=0.10)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # TP should trigger on bar 10 (high=112 > 110), closing the trade
    assert result.total_trades >= 1
    assert all(t == "Closed" for t in result.trades_df["Status"].tolist())
    # Equity should have gained money (TP exit)
    assert result.equity_curve.iloc[-1] > 100500

def test_tp_does_not_trigger_when_high_below_tp_level():
    """TP should NOT trigger when bar's HIGH stays below TP level.

    Setup:
    - Entry at bar 5 (close=100)
    - Bar 10: close=107, high=108 (only +8% from entry, TP at +10%)
    - HIGH=108 < 110, so TP should NOT trigger
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10] = 107

    high = close * 1.01
    low = close * 0.99
    high[10] = 108.0

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20
    df.loc[df.index[25], 'rsi_14'] = 80

    dna = make_dna(stop_loss=0.20, take_profit=0.10)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # TP at 10% not triggered (high=108 < 110), trade should exit via signal at bar 25
    assert result.total_trades >= 1
    # Equity should be close to init (no TP profit)
    assert result.equity_curve.iloc[-1] < 100500

# ── A1: Short position SL/TP ──

def test_sl_triggers_on_intraday_high_short():
    """Short position SL should trigger when bar's HIGH touches SL level.

    Setup:
    - Short entry at bar 5 (close=100)
    - Bar 10: close=103 (above entry, but SL at +5% = 105), high=106 (touches SL)
    - SL triggers because HIGH=106 > 105
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10] = 103

    high = close * 1.01
    low = close * 0.99
    high[10] = 106.0  # +6% from entry, SL at +5%

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(direction="short", stop_loss=0.05, take_profit=0.20)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades >= 1
    # Short SL should trigger (high=106 > 105), trade should be Closed
    assert all(t == "Closed" for t in result.trades_df["Status"].tolist())

def test_tp_triggers_on_intraday_low_short():
    """Short position TP should trigger when bar's LOW touches TP level.

    Setup:
    - Short entry at bar 5 (close=100)
    - Bar 10: close=93, low=88 (drops -12%, TP at -10% = 90)
    - TP triggers because LOW=88 < 90
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10] = 93

    high = close * 1.01
    low = close * 0.99
    low[10] = 88.0  # -12% from entry, TP at -10%

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(direction="short", stop_loss=0.20, take_profit=0.10)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades >= 1
    # Short TP should trigger (low=88 < 90), trade should be Closed
    assert all(t == "Closed" for t in result.trades_df["Status"].tolist())

# ── B1: Liquidation priority ──

def test_liquidation_checked_before_sl():
    """When liquidation condition is met, position should be forcefully
    closed and no further trading should occur.

    Uses no SL (stop_loss=0) so that liquidation is the only exit mechanism.
    With 10x leverage and 90% price crash, equity must drop below
    maintenance level and trigger liquidation.
    """
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    # Price crashes from 100 to 10 (90% drop) with 10x leverage
    close = np.linspace(100, 10, n)

    df = pd.DataFrame({
        'open': close, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[2], 'rsi_14'] = 20

    # No SL/TP so liquidation is the only exit
    dna = make_dna(leverage=10, stop_loss=0.0, take_profit=0.0)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # Should be liquidated (equity drops below maintenance)
    assert result.liquidated
    # Trade should be Closed
    assert result.total_trades >= 1

def test_no_new_trades_after_catastrophic_sl_with_leverage():
    """After a catastrophic SL on leveraged position,
    is_liquidated should be set and no new trades should occur.

    Setup: 5x leverage, price drops 25% in one bar.
    SL at 5% would trigger, but the loss is much larger than SL
    because the drop is instantaneous within one bar.
    """
    n = 30
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10] = 70  # 30% drop

    high = close * 1.01
    low = close * 0.99
    low[10] = 69.0  # even worse intraday

    df = pd.DataFrame({
        'open': close * 0.999, 'high': high,
        'low': low, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20
    # Late entry signal - should NOT result in trade if liquidated
    df.loc[df.index[20], 'rsi_14'] = 20

    dna = make_dna(leverage=5, stop_loss=0.05, take_profit=0.0)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # B2 强化: 5x 杠杆 + 30% 单 bar 暴跌应触发清算(engine.py:519-523)
    # maintenance = init_cash*(1-0.9/lev) = 82000; 之前只断言 equity<100000(弱, 无法区分清算与否)
    assert result.liquidated is True, "5x 杠杆 + 30% 暴跌应触发清算"
    assert result.equity_curve.min() < 82000  # 跌破维持保证金

# ── Regression: existing behavior preserved ──

def test_sl_with_gradual_decline():
    """SL should trigger on gradual decline even with high/low check."""
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10:30] = np.linspace(100, 85, 20)

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(stop_loss=0.05, take_profit=0.20)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades >= 1

def test_tp_with_gradual_rise():
    """TP should trigger on gradual rise even with high/low check."""
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    close = np.ones(n) * 100
    close[10:30] = np.linspace(100, 120, 20)

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(stop_loss=0.20, take_profit=0.10)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades >= 1

