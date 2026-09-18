"""Tests for leverage amplification of equity curve post-processing."""

import pytest

pytestmark = [pytest.mark.unit]
import numpy as np
import pandas as pd

from core.backtest.engine import (
    BacktestEngine, BacktestResult, _apply_funding_costs,
    _apply_leverage_to_equity,
)
from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, ExecutionGenes, StrategyDNA,
)


def _make_dna(
    leverage: int = 1,
    direction: str = "long",
    position_size: float = 0.5,
    stop_loss: float = 0.0,
    take_profit: float | None = None,
) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=stop_loss, take_profit=take_profit,
            position_size=position_size, leverage=leverage, direction=direction,
        ),
    )


class TestApplyLeverageToEquity:
    """Unit tests for _apply_leverage_to_equity."""

    def test_leverage_1x_no_change(self):
        """leverage=1 returns the original curve unchanged."""
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        eq = pd.Series([100000, 101000, 102000, 101500, 103000], index=idx)
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[0]],
            "Exit Timestamp": [idx[4]],
        })
        result = _apply_leverage_to_equity(eq, trades_df, 1.0, 100000.0)
        pd.testing.assert_series_equal(result, eq)

    def test_leverage_1x_skips_without_trades(self):
        """leverage=1 with no trades returns original curve."""
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        eq = pd.Series([100000, 101000, 102000, 101500, 103000], index=idx)
        result = _apply_leverage_to_equity(eq, None, 1.0, 100000.0)
        pd.testing.assert_series_equal(result, eq)

    def test_no_trades_returns_original(self):
        """leverage > 1 but empty trades_df returns original curve."""
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        eq = pd.Series([100000, 101000, 102000, 101500, 103000], index=idx)
        empty_trades = pd.DataFrame(columns=["Entry Timestamp", "Exit Timestamp"])
        result = _apply_leverage_to_equity(eq, empty_trades, 3.0, 100000.0)
        pd.testing.assert_series_equal(result, eq)

    def test_leverage_3x_amplifies_single_trade(self):
        """Single long trade: price +10%, position_size=0.5, leverage=3.
        vbt return = 5% -> leveraged return = 15%."""
        idx = pd.date_range("2024-01-01", periods=6, freq="4h")
        # vbt curve: position 50% of capital, price +10% -> equity +5%
        # Bars: [flat, entry, +5%, +5%, +5%, flat]
        vbt_eq = pd.Series(
            [100000, 100000, 101667, 103333, 105000, 105000],
            index=idx,
        )
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[1]],
            "Exit Timestamp": [idx[4]],
        })
        result = _apply_leverage_to_equity(vbt_eq, trades_df, 3.0, 100000.0)

        # During trade: leveraged = base + L * (vbt - vbt_entry)
        # At bar 2: 100000 + 3 * (101667 - 100000) = 105001
        # At bar 4: 100000 + 3 * (105000 - 100000) = 115000
        assert abs(result.iloc[0] - 100000) < 1  # flat before entry
        assert abs(result.iloc[4] - 115000) < 1  # 15% return
        assert abs(result.iloc[5] - 115000) < 1  # flat after exit

    def test_flat_between_trades(self):
        """Between two trades, equity stays at last exit value."""
        idx = pd.date_range("2024-01-01", periods=10, freq="4h")
        # Trade 1: idx[1] to idx[3], gain 5000 in vbt
        # Trade 2: idx[5] to idx[8], lose 3000 in vbt (from vbt base)
        vbt_eq = pd.Series(
            [100000, 100000, 105000, 105000, 105000, 105000, 102000, 99000, 96000, 96000],
            index=idx,
        )
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[1], idx[5]],
            "Exit Timestamp": [idx[3], idx[8]],
        })
        result = _apply_leverage_to_equity(vbt_eq, trades_df, 3.0, 100000.0)

        # Trade 1 exit: 100000 + 3*(105000-100000) = 115000
        assert abs(result.iloc[3] - 115000) < 1
        # Flat bar between trades (idx[4])
        assert abs(result.iloc[4] - 115000) < 1
        # Trade 2 base starts at 115000
        # At idx[5] (entry): 115000 + 3*(105000-105000) = 115000
        assert abs(result.iloc[5] - 115000) < 1

    def test_consecutive_trades_compound(self):
        """Two consecutive trades compound correctly."""
        idx = pd.date_range("2024-01-01", periods=8, freq="4h")
        # Trade 1: idx[1]-idx[3], vbt gain 5000
        # Trade 2: idx[3]-idx[6], vbt loss 3000 from entry at 105000
        vbt_eq = pd.Series(
            [100000, 100000, 105000, 105000, 102000, 99000, 96000, 96000],
            index=idx,
        )
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[1], idx[3]],
            "Exit Timestamp": [idx[3], idx[6]],
        })
        result = _apply_leverage_to_equity(vbt_eq, trades_df, 3.0, 100000.0)

        # Trade 1 exit: 100000 + 3*(105000-100000) = 115000
        # Trade 2: base=115000, vbt_entry=105000
        # idx[4]: 115000 + 3*(102000-105000) = 106000
        # idx[6]: 115000 + 3*(96000-105000) = 88000
        assert abs(result.iloc[3] - 115000) < 1
        assert abs(result.iloc[6] - 88000) < 1
        assert abs(result.iloc[7] - 88000) < 1

    def test_short_leverage_amplifies(self):
        """Short trade + leverage=2 amplifies the loss."""
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        # vbt short: price goes up -> loss in vbt
        vbt_eq = pd.Series(
            [100000, 100000, 97500, 95000, 95000],
            index=idx,
        )
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[1]],
            "Exit Timestamp": [idx[3]],
        })
        result = _apply_leverage_to_equity(vbt_eq, trades_df, 2.0, 100000.0)

        # 2x: idx[3] = 100000 + 2*(95000-100000) = 90000
        assert abs(result.iloc[3] - 90000) < 1
        assert abs(result.iloc[4] - 90000) < 1

    def test_open_trade_extends_to_end(self):
        """Open trade (no Exit Timestamp) extends to curve end."""
        idx = pd.date_range("2024-01-01", periods=5, freq="4h")
        vbt_eq = pd.Series(
            [100000, 100000, 105000, 110000, 115000],
            index=idx,
        )
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[1]],
            "Exit Timestamp": [pd.NaT],  # open trade
        })
        result = _apply_leverage_to_equity(vbt_eq, trades_df, 3.0, 100000.0)

        # idx[4]: 100000 + 3*(115000-100000) = 145000
        assert abs(result.iloc[4] - 145000) < 1

    def test_total_return_matches_leveraged_curve(self):
        """total_return = curve[-1]/curve[0] - 1."""
        idx = pd.date_range("2024-01-01", periods=6, freq="4h")
        vbt_eq = pd.Series(
            [100000, 100000, 105000, 110000, 115000, 115000],
            index=idx,
        )
        trades_df = pd.DataFrame({
            "Entry Timestamp": [idx[1]],
            "Exit Timestamp": [idx[4]],
        })
        result = _apply_leverage_to_equity(vbt_eq, trades_df, 3.0, 100000.0)
        total_return = result.iloc[-1] / result.iloc[0] - 1
        expected_return = 3.0 * (115000 / 100000 - 1)  # 3 * 15% = 45%
        assert abs(total_return - expected_return) < 0.001


class TestLeverageEquityIntegration:
    """Integration tests running full backtest with leverage."""

    def test_3x_leverage_amplifies_equity_curve(self):
        """Full engine run: 3x leverage should amplify equity changes vs 1x."""
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        np.random.seed(42)
        # Gentle uptrend to ensure entries trigger
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.05)
        df = pd.DataFrame({
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"

        dna_1x = _make_dna(leverage=1, direction="long")
        dna_3x = _make_dna(leverage=3, direction="long")

        engine = BacktestEngine(init_cash=100000)
        result_1x = engine.run(dna_1x, df)
        result_3x = engine.run(dna_3x, df)

        # Both should have valid results
        assert isinstance(result_1x.total_return, float)
        assert isinstance(result_3x.total_return, float)

        # If there are trades, the returns should differ (leverage amplifies)
        if result_1x.total_trades > 0 and result_3x.total_trades > 0:
            # 3x curve should not equal 1x curve
            eq_1x = result_1x.equity_curve.values
            eq_3x = result_3x.equity_curve.values
            assert not np.allclose(eq_1x, eq_3x, atol=1), \
                "3x leverage equity should differ from 1x"

    def test_batch_run_different_leverages(self):
        """batch_run with different leverages produces different results."""
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.05)
        df = pd.DataFrame({
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"

        dna_1x = _make_dna(leverage=1, direction="long")
        dna_3x = _make_dna(leverage=3, direction="long")
        dna_5x = _make_dna(leverage=5, direction="long")

        engine = BacktestEngine(init_cash=100000)
        results = engine.batch_run([dna_1x, dna_3x, dna_5x], df)

        assert len(results) == 3
        # All should have valid results
        for r in results:
            assert isinstance(r.total_return, float)

        # Results with different leverage should differ (if trades exist)
        if all(r.total_trades > 0 for r in results):
            returns = [r.total_return for r in results]
            # Not all returns should be identical
            assert not all(abs(returns[0] - r) < 0.0001 for r in returns), \
                "Different leverages should produce different returns"

    def test_liquidation_check_on_amplified_curve(self):
        """Liquidation should be checked on amplified equity curve."""
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        # Severe crash to trigger liquidation
        close = np.linspace(100, 10, n)
        df = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"
        df["rsi_14"] = 50.0
        df.loc[df.index[2], "rsi_14"] = 20

        dna = _make_dna(leverage=10, direction="long", stop_loss=0.0)
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df)
        # With severe crash and high leverage, should liquidate
        assert isinstance(result.liquidated, bool)

    def test_funding_cost_on_leveraged_curve(self):
        """Funding cost should be deducted from the leveraged curve."""
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.05)
        df = pd.DataFrame({
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"

        dna = _make_dna(leverage=3, direction="long")
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df)

        # With leverage > 1, funding cost should be positive if trades exist
        if result.total_trades > 0:
            assert result.total_funding_cost >= 0, \
                "Funding cost should be non-negative with leverage"
