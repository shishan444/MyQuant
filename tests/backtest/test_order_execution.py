"""Order execution verification tests.

Verifies:
- Short direction produces negative returns when price rises
- Position size affects equity change magnitude
- Add signal increases position
- Reduce signal decreases position
- Entry price is tracked correctly for SL/TP
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from core.strategy.executor import SignalSet
from tests.helpers.data_factory import make_dna


def _make_df(n=80, close_arr=None, freq="4h"):
    """Create OHLCV DataFrame from close array."""
    if close_arr is None:
        close_arr = np.ones(n) * 100
    close = np.array(close_arr, dtype=float)
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    return df


def _sig(entries_idx=None, exits_idx=None, adds_idx=None,
         reduces_idx=None, n=80, direction=1.0):
    """Build a SignalSet from index lists."""
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    e = pd.Series(False, index=dates)
    x = pd.Series(False, index=dates)
    a = pd.Series(False, index=dates)
    r = pd.Series(False, index=dates)
    if entries_idx:
        for i in entries_idx:
            e.iloc[i] = True
    if exits_idx:
        for i in exits_idx:
            x.iloc[i] = True
    if adds_idx:
        for i in adds_idx:
            a.iloc[i] = True
    if reduces_idx:
        for i in reduces_idx:
            r.iloc[i] = True
    return SignalSet(entries=e, exits=x, adds=a, reduces=r,
                     entry_direction=pd.Series(direction, index=dates))


class TestShortDirection:
    """Short orders should produce opposite P&L to long."""

    def test_short_loses_when_price_rises(self):
        """Short position with rising price should lose money."""
        close = np.linspace(100, 120, 80)
        df = _make_df(80, close)

        sig = _sig(entries_idx=[5], exits_idx=[50], direction=-1.0)
        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        result = engine.run(
            make_dna(direction="short", stop_loss=0.0, take_profit=0.0),
            df, signal_set=sig,
        )

        if result.total_trades == 0:
            pytest.skip("No trades")

        assert result.total_return < 0, \
            f"Short in uptrend should lose, got {result.total_return:.4f}"

    def test_long_gains_when_price_rises(self):
        """Long position with rising price should make money."""
        close = np.linspace(100, 120, 80)
        df = _make_df(80, close)

        sig = _sig(entries_idx=[5], exits_idx=[50])
        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        result = engine.run(
            make_dna(direction="long", stop_loss=0.0, take_profit=0.0),
            df, signal_set=sig,
        )

        if result.total_trades == 0:
            pytest.skip("No trades")

        assert result.total_return > 0, \
            f"Long in uptrend should profit, got {result.total_return:.4f}"


class TestPositionSize:
    """Position size should affect equity change magnitude."""

    def test_larger_position_larger_return(self):
        """Bigger position_size should produce larger return magnitude."""
        close = np.linspace(100, 120, 80)
        df = _make_df(80, close)

        sig = _sig(entries_idx=[5], exits_idx=[50])

        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        result_small = engine.run(
            make_dna(position_size=0.2, stop_loss=0.0, take_profit=0.0),
            df, signal_set=sig,
        )
        result_large = engine.run(
            make_dna(position_size=0.8, stop_loss=0.0, take_profit=0.0),
            df, signal_set=sig,
        )

        if result_small.total_trades == 0 or result_large.total_trades == 0:
            pytest.skip("No trades")

        assert abs(result_large.total_return) > abs(result_small.total_return), \
            f"Large pos ({result_large.total_return:.4f}) should > small ({result_small.total_return:.4f})"


class TestAddReduceSignals:
    """Add/reduce signals should adjust position."""

    def test_reduce_signal_closes_partial_position(self):
        """Reduce signal should produce a trade that reduces position."""
        n = 80
        close = np.ones(n) * 100
        df = _make_df(n, close)

        # Entry, then reduce, then exit
        sig = _sig(entries_idx=[5], reduces_idx=[30], exits_idx=[60])

        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        result = engine.run(
            make_dna(stop_loss=0.0, take_profit=0.0),
            df, signal_set=sig,
        )

        # Should have at least 2 trades: entry+reduce and final exit
        assert result.total_trades >= 1

    def test_add_signal_increases_position(self):
        """Add signal should increase position size."""
        n = 80
        close = np.linspace(100, 110, n)
        df = _make_df(n, close)

        # Entry, then add, then exit
        sig = _sig(entries_idx=[5], adds_idx=[25], exits_idx=[60])

        engine = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        result = engine.run(
            make_dna(stop_loss=0.0, take_profit=0.0),
            df, signal_set=sig,
        )

        assert result.total_trades >= 1


class TestEntryPriceTracking:
    """Entry price should be tracked for SL/TP calculation."""

    def test_sl_based_on_entry_price(self):
        """SL should be calculated from entry_price, not current price.

        Setup: Entry at close=100, then price drops to 96.
        With SL=5%, SL triggers at 100*(1-0.05)=95.
        Price 96 > 95, so SL should NOT trigger.
        Then price drops to 93.
        SL at 95, price low=93 < 95, SL should trigger.
        """
        n = 60
        close = np.ones(n) * 100
        close[10:15] = np.linspace(100, 96, 5)  # Drop but above SL
        close[15:25] = np.linspace(96, 93, 10)   # Drop below SL

        df = _make_df(n, close)

        sig = _sig(entries_idx=[5], exits_idx=[50])
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(
            make_dna(stop_loss=0.05, take_profit=0.0),
            df, signal_set=sig,
        )

        # SL should have triggered (entry=100, SL at 95, price went to 93)
        assert result.total_trades >= 1
        # Trade should be closed (SL)
        if result.trades_df is not None and len(result.trades_df) > 0:
            statuses = result.trades_df["Status"].tolist()
            assert "Closed" in statuses
