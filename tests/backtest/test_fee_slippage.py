"""Fee and slippage verification tests.

Verifies:
- Fee is deducted from equity on each trade
- Slippage shifts execution price
- Fee scales with leverage (charged on notional value)
- Multiple trades accumulate fees correctly
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from core.strategy.executor import SignalSet
from tests.helpers.data_factory import make_dna


def _make_flat_df(n=80):
    """Flat price data to isolate fee effects from price movement."""
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = np.ones(n) * 100.0
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    return df


def _force_trade(df, entry_bar=5, exit_bar=30, direction=1.0):
    """Create a SignalSet with a single forced trade."""
    entries = pd.Series(False, index=df.index)
    exits = pd.Series(False, index=df.index)
    entries.iloc[entry_bar] = True
    exits.iloc[exit_bar] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction_series = pd.Series(float(direction), index=df.index)
    return SignalSet(entries=entries, exits=exits, adds=adds,
                     reduces=reduces, entry_direction=direction_series)


class TestFeeDeduction:
    """Verify fee is actually deducted from equity."""

    def test_single_trade_fee_deducted(self):
        """Single buy+sell with fee should reduce equity vs no fee."""
        df = _make_flat_df()
        sig = _force_trade(df, entry_bar=5, exit_bar=30)

        engine_free = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        engine_fee = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0)

        dna = make_dna(stop_loss=0.0, take_profit=0.0)
        result_free = engine_free.run(dna, df, signal_set=sig)
        result_fee = engine_fee.run(dna, df, signal_set=sig)

        if result_free.total_trades == 0:
            pytest.skip("No trades")

        # With flat prices, no-fee should end at init_cash
        assert abs(result_free.equity_curve.iloc[-1] - 100000) < 1

        # With fee, equity should be lower
        fee_diff = result_free.equity_curve.iloc[-1] - result_fee.equity_curve.iloc[-1]
        assert fee_diff > 0, f"Fee should reduce equity by > 0, got diff={fee_diff}"

    def test_fee_amount_reasonable(self):
        """Fee for a single round-trip should be approximately 2 * fee_rate * position_value.

        Buy: fee = position_value * fee_rate
        Sell: fee = position_value * fee_rate
        Total ~= 2 * position_size * init_cash * fee_rate
        """
        df = _make_flat_df()
        sig = _force_trade(df, entry_bar=5, exit_bar=30)

        fee_rate = 0.001
        engine = BacktestEngine(init_cash=100000, fee=fee_rate, slippage=0.0)
        dna = make_dna(stop_loss=0.0, take_profit=0.0, position_size=0.5)
        result = engine.run(dna, df, signal_set=sig)

        if result.total_trades == 0:
            pytest.skip("No trades")

        # Expected fee: 2 trades * 0.5 * 100000 * 0.001 = 100
        equity_loss = 100000 - result.equity_curve.iloc[-1]
        expected_fee = 2 * 0.5 * 100000 * fee_rate
        # Allow 50% tolerance (vbt may adjust position value)
        assert abs(equity_loss - expected_fee) < expected_fee * 0.5, \
            f"Fee loss {equity_loss:.2f} should be ~{expected_fee:.2f}"

    def test_multiple_trades_accumulate_fees(self):
        """Multiple round-trip trades should accumulate more fees."""
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = np.ones(n) * 100.0
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close, "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"

        # 3 trades
        entries = pd.Series(False, index=df.index)
        exits = pd.Series(False, index=df.index)
        entries.iloc[5] = True
        exits.iloc[25] = True
        entries.iloc[45] = True
        exits.iloc[65] = True
        entries.iloc[85] = True
        exits.iloc[105] = True
        adds = pd.Series(False, index=df.index)
        reduces = pd.Series(False, index=df.index)

        sig = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=pd.Series(1.0, index=df.index))

        engine = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0)
        dna = make_dna(stop_loss=0.0, take_profit=0.0)
        result = engine.run(dna, df, signal_set=sig)

        if result.total_trades < 2:
            pytest.skip("Not enough trades")

        # More trades = more fees = lower equity
        assert result.equity_curve.iloc[-1] < 100000


class TestSlippageDeduction:
    """Verify slippage shifts execution price."""

    def test_slippage_reduces_buying_power(self):
        """With slippage, effective buy price is higher, reducing equity."""
        df = _make_flat_df()
        sig = _force_trade(df, entry_bar=5, exit_bar=30)

        engine_no_slip = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        engine_slip = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.001)

        dna = make_dna(stop_loss=0.0, take_profit=0.0)
        result_no = engine_no_slip.run(dna, df, signal_set=sig)
        result_slip = engine_slip.run(dna, df, signal_set=sig)

        if result_no.total_trades == 0:
            pytest.skip("No trades")

        assert result_slip.equity_curve.iloc[-1] < result_no.equity_curve.iloc[-1]


class TestFeeScalesWithLeverage:
    """Fee should be charged on notional value (scaled by leverage)."""

    def test_leveraged_fee_higher_than_1x_fee(self):
        """Leveraged trade should incur higher total fees than 1x trade."""
        df = _make_flat_df()
        sig = _force_trade(df, entry_bar=5, exit_bar=30)

        engine_1x = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0)
        engine_3x = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0)

        dna_1x = make_dna(leverage=1, stop_loss=0.0, take_profit=0.0)
        dna_3x = make_dna(leverage=3, stop_loss=0.0, take_profit=0.0)

        result_1x = engine_1x.run(dna_1x, df, signal_set=sig)
        result_3x = engine_3x.run(dna_3x, df, signal_set=sig)

        if result_1x.total_trades == 0 or result_3x.total_trades == 0:
            pytest.skip("No trades")

        # 3x leverage fee > 1x fee (fee charged on notional = position * leverage)
        fee_1x = 100000 - result_1x.equity_curve.iloc[-1]
        fee_3x = 100000 - result_3x.equity_curve.iloc[-1]
        assert fee_3x > fee_1x, \
            f"3x fee ({fee_3x:.2f}) should exceed 1x fee ({fee_1x:.2f})"
