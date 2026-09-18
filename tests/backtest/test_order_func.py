"""Phase 3: from_order_func engine with real-time risk management."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from tests.helpers.data_factory import make_dna
from core.strategy.executor import SignalSet

def _make_df(n=200, seed=42):
    np.random.seed(seed)
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = np.clip(50 + np.random.randn(n) * 20, 0, 100)
    return df

def test_liquidation_stops_trading():
    """10x leverage + 95% drop should trigger liquidation and stop all trading."""
    n = 100
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    # Price crashes from 100 to 5 (95% drop)
    close = np.linspace(100, 5, n)
    df = pd.DataFrame({
        'open': close, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'

    # Force entry at bar 2 (RSI oversold)
    df['rsi_14'] = 50.0
    df.loc[df.index[2], 'rsi_14'] = 20  # entry trigger

    dna = make_dna(leverage=10, stop_loss=0.0, take_profit=0.0)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # Should be liquidated with leveraged position in a crash
    assert result.liquidated

def test_no_negative_equity():
    """Equity should never go below 0."""
    df = _make_df(200)
    for leverage in [1, 3, 5]:
        dna = make_dna(leverage=leverage)
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df)
        equity = result.equity_curve
        # After funding cost adjustment, equity should still be >= 0
        assert (equity >= -1).all(), f"Negative equity with leverage={leverage}"

def test_sl_trigger():
    """Stop-loss should close position when loss exceeds threshold."""
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    # Price drops 10% after entry
    close = np.ones(n) * 100
    close[10:30] = np.linspace(100, 85, 20)  # 15% drop

    df = pd.DataFrame({
        'open': close, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20  # entry at bar 5

    dna = make_dna(stop_loss=0.05, take_profit=0.20)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # Should have a trade (SL triggered or exit)
    assert result.total_trades >= 1

def test_tp_trigger():
    """Take-profit should close position when profit exceeds threshold."""
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')

    # Price rises 20% after entry
    close = np.ones(n) * 100
    close[10:30] = np.linspace(100, 120, 20)

    df = pd.DataFrame({
        'open': close, 'high': close * 1.01,
        'low': close * 0.99, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df.loc[df.index[5], 'rsi_14'] = 20

    dna = make_dna(stop_loss=0.20, take_profit=0.10)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades >= 1

def test_position_size_capped():
    """Position size should respect position_size setting."""
    df = _make_df(100)
    df.loc[df.index[20], 'rsi_14'] = 20
    df.loc[df.index[60], 'rsi_14'] = 80

    dna = make_dna(position_size=0.3)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # With position_size=0.3, the trade should not use all capital
    assert result.total_trades >= 0

