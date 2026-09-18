"""Invariant tests: mathematical properties that must always hold.

These tests verify cross-cutting mathematical relationships rather than
specific module behavior. Each test changes one input parameter and
verifies the output changes by the expected mathematical factor.

Covers:
- Leverage amplification invariant
- Fee/slippage deduction invariant
- Direction symmetry invariant
- Scoring monotonicity invariant
- total_return / equity_curve consistency
- No-trade invariants (zero cost, no change)
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
from tests.helpers.data_factory import make_ohlcv, make_dna, make_engine


def _make_trending_df(n=100, start_price=100, end_price=120, freq="4h"):
    """Create OHLCV data with a deterministic uptrend."""
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = np.linspace(start_price, end_price, n)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    df["rsi_14"] = 50.0
    return df


def _make_declining_df(n=100, start_price=120, end_price=80, freq="4h"):
    """Create OHLCV data with a deterministic downtrend."""
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = np.linspace(start_price, end_price, n)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    df["rsi_14"] = 50.0
    return df


def _force_entry_exit(df, entry_bar=5, exit_bar=50):
    """Return a SignalSet with a forced entry and exit."""
    entries = pd.Series(False, index=df.index)
    exits = pd.Series(False, index=df.index)
    entries.iloc[entry_bar] = True
    exits.iloc[exit_bar] = True
    adds = pd.Series(False, index=df.index)
    reduces = pd.Series(False, index=df.index)
    direction = pd.Series(1.0, index=df.index)
    return SignalSet(entries=entries, exits=exits, adds=adds,
                     reduces=reduces, entry_direction=direction)


# -- Leverage invariants --

class TestLeverageInvariants:
    """Leverage must amplify both gains and losses."""

    def test_leverage_amplifies_profit(self):
        """Leveraged long in uptrend should amplify positive return."""
        df = _make_trending_df(n=100, end_price=130)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine = make_engine()
        result_1x = engine.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_3x = engine.run(make_dna(leverage=3, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_1x.total_trades == 0 or result_3x.total_trades == 0:
            pytest.skip("No trades generated")

        # 3x should have larger absolute return than 1x
        assert abs(result_3x.total_return) > abs(result_1x.total_return), \
            f"3x return ({result_3x.total_return:.4f}) should exceed 1x ({result_1x.total_return:.4f})"

    def test_leverage_amplifies_loss(self):
        """Leveraged long in downtrend should amplify negative return."""
        df = _make_declining_df(n=100, end_price=80)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine = make_engine()
        result_1x = engine.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_3x = engine.run(make_dna(leverage=3, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_1x.total_trades == 0 or result_3x.total_trades == 0:
            pytest.skip("No trades generated")

        # Both should be negative, 3x more so
        assert result_1x.total_return < 0, "1x should be negative in downtrend"
        assert result_3x.total_return < result_1x.total_return, \
            f"3x loss ({result_3x.total_return:.4f}) should exceed 1x loss ({result_1x.total_return:.4f})"

    def test_leverage_equity_differs_from_1x(self):
        """Equity curves for leverage=1 and leverage=3 must differ."""
        df = _make_trending_df(n=100, end_price=130)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine = make_engine()
        result_1x = engine.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_3x = engine.run(make_dna(leverage=3, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_1x.total_trades == 0:
            pytest.skip("No trades generated")

        assert not np.allclose(result_1x.equity_curve, result_3x.equity_curve, atol=1), \
            "Equity curves must differ between 1x and 3x leverage"

    def test_leverage_1x_no_funding_cost(self):
        """1x leverage should always have zero funding cost."""
        df = _make_trending_df(n=100)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine = make_engine()
        result = engine.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        assert result.total_funding_cost == 0.0

    def test_leverage_higher_means_higher_funding(self):
        """Higher leverage should produce higher funding costs."""
        df = _make_trending_df(n=100, end_price=110)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine = make_engine()
        result_2x = engine.run(make_dna(leverage=2, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_5x = engine.run(make_dna(leverage=5, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_2x.total_trades == 0 or result_5x.total_trades == 0:
            pytest.skip("No trades generated")

        assert result_5x.total_funding_cost > result_2x.total_funding_cost, \
            f"5x funding ({result_5x.total_funding_cost:.2f}) > 2x ({result_2x.total_funding_cost:.2f})"


# -- Fee/slippage invariants --

class TestFeeSlippageInvariants:
    """Fee and slippage must reduce equity."""

    def test_fee_reduces_equity_vs_zero_fee(self):
        """Trades with fee should have lower final equity than zero fee."""
        df = _make_trending_df(n=100, end_price=130)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine_free = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        engine_paid = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0)

        result_free = engine_free.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_paid = engine_paid.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_free.total_trades == 0:
            pytest.skip("No trades generated")

        assert result_paid.equity_curve.iloc[-1] < result_free.equity_curve.iloc[-1], \
            f"With fee: {result_paid.equity_curve.iloc[-1]:.2f} should < no fee: {result_free.equity_curve.iloc[-1]:.2f}"

    def test_slippage_reduces_equity_vs_zero_slippage(self):
        """Trades with slippage should have lower equity than zero slippage."""
        df = _make_trending_df(n=100, end_price=130)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine_no_slip = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        engine_slip = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.001)

        result_no = engine_no_slip.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_slip = engine_slip.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_no.total_trades == 0:
            pytest.skip("No trades generated")

        assert result_slip.equity_curve.iloc[-1] < result_no.equity_curve.iloc[-1], \
            f"With slippage: {result_slip.equity_curve.iloc[-1]:.2f} < no slippage: {result_no.equity_curve.iloc[-1]:.2f}"

    def test_higher_fee_worse_than_lower_fee(self):
        """fee=0.002 should produce lower equity than fee=0.001."""
        df = _make_trending_df(n=100, end_price=130)
        sig_set = _force_entry_exit(df, entry_bar=3, exit_bar=60)

        engine_low = BacktestEngine(init_cash=100000, fee=0.001, slippage=0.0)
        engine_high = BacktestEngine(init_cash=100000, fee=0.002, slippage=0.0)

        result_low = engine_low.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)
        result_high = engine_high.run(make_dna(leverage=1, stop_loss=0.0, take_profit=0.0), df, signal_set=sig_set)

        if result_low.total_trades == 0:
            pytest.skip("No trades generated")

        assert result_high.equity_curve.iloc[-1] < result_low.equity_curve.iloc[-1]


# -- Direction invariants --

class TestDirectionInvariants:
    """Long vs Short should produce opposite P&L signs."""

    def test_long_profits_in_uptrend_short_losses(self):
        """Long gains when price rises, short loses when price rises."""
        df = _make_trending_df(n=100, end_price=130)

        entries_long = pd.Series(False, index=df.index)
        entries_long.iloc[3] = True
        exits = pd.Series(False, index=df.index)
        exits.iloc[60] = True
        adds = pd.Series(False, index=df.index)
        reduces = pd.Series(False, index=df.index)

        sig_long = SignalSet(entries=entries_long, exits=exits, adds=adds,
                             reduces=reduces, entry_direction=pd.Series(1.0, index=df.index))
        sig_short = SignalSet(entries=entries_long, exits=exits, adds=adds,
                              reduces=reduces, entry_direction=pd.Series(-1.0, index=df.index))

        engine = make_engine()
        result_long = engine.run(make_dna(direction="long", stop_loss=0.0, take_profit=0.0), df, signal_set=sig_long)
        result_short = engine.run(make_dna(direction="short", stop_loss=0.0, take_profit=0.0), df, signal_set=sig_short)

        if result_long.total_trades == 0 or result_short.total_trades == 0:
            pytest.skip("No trades generated")

        assert result_long.total_return > 0, f"Long in uptrend should profit, got {result_long.total_return:.4f}"
        assert result_short.total_return < 0, f"Short in uptrend should lose, got {result_short.total_return:.4f}"

    def test_short_profits_in_downtrend(self):
        """Short gains when price drops."""
        df = _make_declining_df(n=100, end_price=70)
        entries = pd.Series(False, index=df.index)
        entries.iloc[3] = True
        exits = pd.Series(False, index=df.index)
        exits.iloc[60] = True
        adds = pd.Series(False, index=df.index)
        reduces = pd.Series(False, index=df.index)

        sig = SignalSet(entries=entries, exits=exits, adds=adds, reduces=reduces,
                        entry_direction=pd.Series(-1.0, index=df.index))

        engine = make_engine()
        result = engine.run(make_dna(direction="short", stop_loss=0.0, take_profit=0.0), df, signal_set=sig)

        if result.total_trades == 0:
            pytest.skip("No trades generated")

        assert result.total_return > 0, f"Short in downtrend should profit, got {result.total_return:.4f}"


# -- Scoring invariants --

class TestScoringInvariants:
    """Scoring must be monotonic: better equity => higher score."""

    def test_better_equity_scores_higher(self):
        """A rising equity curve should score higher than a flat one."""
        from core.scoring.metrics import compute_metrics
        from core.scoring.scorer import score_strategy

        rising = pd.Series(np.linspace(100000, 130000, 200))
        flat = pd.Series(np.full(200, 100000.0))

        metrics_rising = compute_metrics(rising, total_trades=10, bars_per_year=2190)
        metrics_flat = compute_metrics(flat, total_trades=10, bars_per_year=2190)

        score_rising = score_strategy(metrics_rising, "profit_first")
        score_flat = score_strategy(metrics_flat, "profit_first")

        assert score_rising["total_score"] > score_flat["total_score"], \
            f"Rising ({score_rising['total_score']:.1f}) should score > flat ({score_flat['total_score']:.1f})"

    def test_zero_trades_scores_zero(self):
        """Zero trades must produce score=0."""
        from core.scoring.scorer import score_strategy
        metrics = {"total_trades": 0, "sharpe_ratio": 0, "annual_return": 0,
                   "max_drawdown": 0, "win_rate": 0}
        result = score_strategy(metrics, "profit_first")
        assert result["total_score"] == 0.0

    def test_liquidated_scores_zero(self):
        """Liquidated strategy must score 0 regardless of metrics."""
        from core.scoring.scorer import score_strategy
        metrics = {"total_trades": 50, "sharpe_ratio": 2.0, "annual_return": 0.5,
                   "max_drawdown": -0.1, "win_rate": 0.6}
        result = score_strategy(metrics, "profit_first", liquidated=True)
        assert result["total_score"] == 0.0


# -- Consistency invariants --

class TestConsistencyInvariants:
    """Output fields must be internally consistent."""

    def test_total_return_matches_equity_curve(self):
        """total_return must equal curve[-1]/curve[0] - 1."""
        df = make_ohlcv(n=200, seed=123)
        df["rsi_14"] = 50.0
        df.loc[df.index[5], "rsi_14"] = 20
        df.loc[df.index[80], "rsi_14"] = 80

        engine = make_engine()
        result = engine.run(make_dna(stop_loss=0.0, take_profit=0.0), df)

        if result.total_trades == 0:
            pytest.skip("No trades")

        curve_return = result.equity_curve.iloc[-1] / result.equity_curve.iloc[0] - 1
        assert abs(curve_return - result.total_return) < 0.001, \
            f"curve_return={curve_return:.4f} vs total_return={result.total_return:.4f}"

    def test_equity_starts_at_init_cash(self):
        """Equity curve must start at init_cash."""
        df = make_ohlcv(n=100)
        df["rsi_14"] = 50.0
        df.loc[df.index[5], "rsi_14"] = 20

        engine = BacktestEngine(init_cash=50000)
        result = engine.run(make_dna(stop_loss=0.0, take_profit=0.0), df)

        assert abs(result.equity_curve.iloc[0] - 50000) < 1

    def test_no_trades_no_equity_change(self):
        """When no trades occur, equity should stay at init_cash."""
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = np.ones(n) * 100
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close, "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"
        df["rsi_14"] = 50.0  # No entry signal

        engine = make_engine()
        result = engine.run(make_dna(stop_loss=0.0, take_profit=0.0), df)

        assert result.total_trades == 0
        assert abs(result.equity_curve.iloc[-1] - 100000) < 1

    def test_no_trades_zero_funding(self):
        """No trades means zero funding cost even with high leverage."""
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = np.ones(n) * 100
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close, "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"
        df["rsi_14"] = 50.0

        engine = make_engine()
        result = engine.run(make_dna(leverage=5, stop_loss=0.0, take_profit=0.0), df)

        assert result.total_funding_cost == 0.0

    def test_max_drawdown_non_positive(self):
        """max_drawdown must be <= 0."""
        df = make_ohlcv(n=200, seed=99)
        df["rsi_14"] = 50.0
        df.loc[df.index[5], "rsi_14"] = 20
        df.loc[df.index[80], "rsi_14"] = 80

        engine = make_engine()
        result = engine.run(make_dna(stop_loss=0.0, take_profit=0.0), df)

        assert result.max_drawdown <= 0, f"max_drawdown={result.max_drawdown} must be <= 0"

    def test_win_rate_between_zero_and_one(self):
        """win_rate must be in [0, 1]."""
        df = make_ohlcv(n=200, seed=77)
        df["rsi_14"] = 50.0
        df.loc[df.index[5], "rsi_14"] = 20
        df.loc[df.index[80], "rsi_14"] = 80

        engine = make_engine()
        result = engine.run(make_dna(stop_loss=0.0, take_profit=0.0), df)

        if result.total_trades > 0:
            assert 0.0 <= result.win_rate <= 1.0


# -- Batch run invariant --

class TestBatchRunInvariants:
    """batch_run must produce consistent per-individual results."""

    def test_batch_single_matches_run(self):
        """batch_run with 1 individual must match run()."""
        df = make_ohlcv(n=200, seed=42)
        df["rsi_14"] = 50.0
        df.loc[df.index[5], "rsi_14"] = 20
        df.loc[df.index[80], "rsi_14"] = 80

        dna = make_dna(stop_loss=0.0, take_profit=0.0)
        engine = make_engine()

        single = engine.run(dna, df)
        batch = engine.batch_run([dna], df)

        assert len(batch) == 1
        assert batch[0].total_trades == single.total_trades

    def test_batch_different_leverages_produce_different_results(self):
        """batch_run with different leverages must produce different returns."""
        df = make_ohlcv(n=200, seed=42)
        df["rsi_14"] = 50.0
        df.loc[df.index[5], "rsi_14"] = 20
        df.loc[df.index[80], "rsi_14"] = 80

        dna_1 = make_dna(leverage=1, stop_loss=0.0, take_profit=0.0)
        dna_3 = make_dna(leverage=3, stop_loss=0.0, take_profit=0.0)

        engine = make_engine()
        results = engine.batch_run([dna_1, dna_3], df)

        if results[0].total_trades > 0 and results[1].total_trades > 0:
            assert not np.allclose(results[0].equity_curve, results[1].equity_curve, atol=1), \
                "1x and 3x leverage must produce different equity curves"
