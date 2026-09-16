"""Boundary and exception tests for executor.py, backtest engine.py, and validation/engine.py.

Covers edge cases: missing keys, empty inputs, extreme values, conflicting signals,
unknown condition types, and single-bar DataFrames.
"""

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

from core.strategy.executor import (
    evaluate_condition,
    combine_signals,
    evaluate_layer,
    resample_signals,
    SignalSet,
    dna_to_signal_set,
)
from core.strategy.dna import (
    ConditionType,
    SignalGene,
    LogicGenes,
    RiskGenes,
    ExecutionGenes,
    TimeframeLayer,
    StrategyDNA,
    SignalRole,
)
from tests.helpers.data_factory import (
    make_ohlcv,
    make_dna,
    make_signal_set,
    make_engine,
    make_enhanced_df,
)


# ============================================================================
# executor.py boundary tests
# ============================================================================


class TestCrossAboveNoThreshold:
    """cross_above / cross_below without threshold key -> all-False series."""

    def test_cross_above_no_threshold_returns_false_series(self):
        idx = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
        indicator = pd.Series(np.arange(10, dtype=float), index=idx)
        close = pd.Series(np.full(10, 50.0), index=idx)
        condition = {"type": "cross_above"}  # no "threshold" key

        result = evaluate_condition(indicator, close, condition)

        assert isinstance(result, pd.Series)
        assert len(result) == 10
        assert not result.any()

    def test_cross_below_no_threshold_returns_false_series(self):
        idx = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
        indicator = pd.Series(np.arange(10, dtype=float), index=idx)
        close = pd.Series(np.full(10, 50.0), index=idx)
        condition = {"type": "cross_below"}  # no "threshold" key

        result = evaluate_condition(indicator, close, condition)

        assert isinstance(result, pd.Series)
        assert len(result) == 10
        assert not result.any()


class TestCombineSignalsUnknownLogic:
    """combine_signals with unknown logic string returns first signal copy."""

    def test_combine_signals_unknown_logic_returns_first(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
        s1 = pd.Series([True, False, True, False, True], index=idx)
        s2 = pd.Series([False, True, False, True, False], index=idx)

        result = combine_signals([s1, s2], "XOR")

        assert result.tolist() == [True, False, True, False, True]
        # Verify it is a copy, not the original object
        assert result is not s1


class TestEvaluateLayerAllGenesFail:
    """evaluate_layer where all genes reference unknown indicators -> all-False."""

    def test_evaluate_layer_all_genes_fail_returns_false_series(self):
        idx = pd.date_range("2024-01-01", periods=20, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {
                "open": np.full(20, 100.0),
                "high": np.full(20, 101.0),
                "low": np.full(20, 99.0),
                "close": np.full(20, 100.0),
                "volume": np.full(20, 1000.0),
            },
            index=idx,
        )
        df.index.name = "timestamp"

        # Genes reference indicators that do not exist in df
        fake_gene = SignalGene(
            indicator="NONEXISTENT_INDICATOR",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "lt", "threshold": 30},
        )
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[fake_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        )

        result = evaluate_layer(layer, df)

        assert isinstance(result, SignalSet)
        assert not result.entries.any()
        assert not result.exits.any()
        assert not result.adds.any()
        assert not result.reduces.any()


class TestResampleSignalsEmptyInputs:
    """resample_signals with empty series or empty target index."""

    def test_resample_signals_empty_series(self):
        empty_series = pd.Series(dtype=bool, index=pd.DatetimeIndex([], tz="UTC"))
        target_idx = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")

        result = resample_signals(empty_series, target_idx)

        assert isinstance(result, pd.Series)
        assert len(result) == 5
        assert not result.any()

    def test_resample_signals_empty_target_index(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
        signals = pd.Series([True, False, True, False, True], index=idx)
        empty_target = pd.DatetimeIndex([], tz="UTC")

        result = resample_signals(signals, empty_target)

        assert isinstance(result, pd.Series)
        assert len(result) == 0


class TestEvaluateConditionMissingTypeKey:
    """evaluate_condition without 'type' key raises KeyError."""

    def test_evaluate_condition_missing_type_key_raises(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
        indicator = pd.Series(np.ones(5), index=idx)
        close = pd.Series(np.ones(5) * 50.0, index=idx)
        condition = {}  # no "type" key

        with pytest.raises(KeyError):
            evaluate_condition(indicator, close, condition)


class TestEntriesExitsConflictExitWins:
    """When entries and exits are both True on the same bar, entries are cleared."""

    def test_entries_exits_conflict_exit_wins(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
        n = 5
        df = pd.DataFrame(
            {
                "open": np.full(n, 100.0),
                "high": np.full(n, 101.0),
                "low": np.full(n, 99.0),
                "close": np.full(n, 100.0),
                "volume": np.full(n, 1000.0),
            },
            index=idx,
        )
        df.index.name = "timestamp"

        # Use RSI column that satisfies both entry (lt 30) and exit (gt 70)
        # Simulate by placing a column that evaluate_layer will use
        df["rsi_14"] = [20.0, 20.0, 50.0, 80.0, 80.0]

        # Entry: rsi < 30 (bars 0, 1 are True)
        # Exit:  rsi > 70 (bars 3, 4 are True)
        # No conflict here. To force conflict, make entry and exit overlap.
        # Set rsi = 20 on all bars: entry lt 30 is True everywhere, exit gt 70 nowhere.
        # Instead, directly test the evaluate_layer conflict resolution by using genes that
        # produce overlapping signals.

        # Use two entry genes and two exit genes where they both fire on bar 0,1,3,4
        # Simpler: set rsi so that entry AND exit both fire on some bars
        df["rsi_14"] = [20.0, 50.0, 50.0, 50.0, 80.0]
        # entry (lt 30): bar 0
        # exit (gt 70):  bar 4
        # No overlap. Let's make a custom approach: create genes that check the same column
        # but one fires entry and the other fires exit on the same bars.

        # Use "eq" condition with threshold=100 for both entry and exit on close column
        # Since close=100 everywhere, both fire everywhere -> conflict
        df["close"] = 100.0  # ensure close is exactly 100

        entry_gene = SignalGene(
            indicator="RSI",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt", "threshold": 0},  # rsi > 0 -> always True
        )
        exit_gene = SignalGene(
            indicator="RSI",
            params={"period": 14},
            role=SignalRole.EXIT_TRIGGER,
            condition={"type": "gt", "threshold": 0},  # rsi > 0 -> always True
        )

        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[entry_gene, exit_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        )

        result = evaluate_layer(layer, df)

        # Both entry and exit signals fire on every bar (rsi > 0 is always True)
        # evaluate_layer clears entries where exits are also True (exit wins)
        assert not result.entries.any()
        assert result.exits.all()


class TestDnaToSignalSetSingleBar:
    """dna_to_signal_set with a single-bar DataFrame."""

    def test_dna_to_signal_set_single_bar(self):
        idx = pd.date_range("2024-01-01", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [40000.0],
                "high": [40100.0],
                "low": [39900.0],
                "close": [40050.0],
                "volume": [5000.0],
                "rsi_14": [45.0],
            },
            index=idx,
        )
        df.index.name = "timestamp"

        dna = make_dna(
            indicator="RSI",
            entry_condition="lt",
            entry_value=50,
            exit_condition="gt",
            exit_value=70,
        )

        result = dna_to_signal_set(dna, df)

        assert isinstance(result, SignalSet)
        assert len(result.entries) == 1
        assert len(result.exits) == 1
        # rsi=45 < 50 -> entry True; rsi=45 not > 70 -> exit False
        assert result.entries.iloc[0] is True or result.entries.iloc[0] == True
        assert result.exits.iloc[0] == False


# ============================================================================
# engine.py (backtest) boundary tests
# ============================================================================


class TestBacktestAllZeroSignals:
    """BacktestEngine.run with all-False signals produces no trades."""

    def test_backtest_all_zero_signals(self):
        df = make_enhanced_df(n=50)
        n = len(df)
        all_false = pd.Series(False, index=df.index)

        sig_set = SignalSet(
            entries=all_false,
            exits=all_false,
            adds=all_false,
            reduces=all_false,
        )

        dna = make_dna()
        engine = make_engine()
        result = engine.run(dna, df, signal_set=sig_set)

        assert isinstance(result.total_return, float)
        assert result.total_trades == 0
        # Equity should stay at initial cash (minus any negligible rounding)
        assert abs(result.equity_curve.iloc[-1] - 100000) < 1.0


class TestBacktestAllEntrySignals:
    """Every bar is an entry signal - only the first bar enters since position is held."""

    def test_backtest_all_entry_signals(self):
        df = make_enhanced_df(n=20)
        all_true = pd.Series(True, index=df.index)
        all_false = pd.Series(False, index=df.index)

        sig_set = SignalSet(
            entries=all_true,
            exits=all_false,
            adds=all_false,
            reduces=all_false,
        )

        dna = make_dna()
        engine = make_engine()
        result = engine.run(dna, df, signal_set=sig_set)

        assert result.total_trades >= 1
        assert result.total_trades <= 3  # entries are shifted, position-based
        assert isinstance(result.total_return, float)
        assert not np.isnan(result.total_return)


class TestBacktestExtremeLeverage:
    """leverage=100 produces a valid result without crashing."""

    def test_backtest_extreme_leverage(self):
        df = make_enhanced_df(n=50)
        dna = make_dna(leverage=100)

        engine = make_engine()
        result = engine.run(dna, df)

        assert isinstance(result, type(result))
        assert isinstance(result.total_return, float)
        assert not np.isnan(result.total_return)
        # With 100x leverage, likely liquidated
        assert isinstance(result.liquidated, bool)


class TestBacktestSingleBarWithEntry:
    """Single bar with entry=True."""

    def test_backtest_single_bar_with_entry(self):
        idx = pd.date_range("2024-01-01", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [40000.0],
                "high": [40100.0],
                "low": [39900.0],
                "close": [40050.0],
                "volume": [5000.0],
                "rsi_14": [25.0],
            },
            index=idx,
        )
        df.index.name = "timestamp"

        dna = make_dna(entry_condition="lt", entry_value=30)
        engine = make_engine()

        # Entry is shifted by 1 bar, so on a single-bar DataFrame the shifted
        # entry becomes NaN -> False, resulting in 0 trades.
        result = engine.run(dna, df)

        assert isinstance(result.total_return, float)
        # After shift(1) on a single bar, entry becomes False -> no trade
        assert result.total_trades == 0


class TestBacktestNanClosePrices:
    """DataFrame with NaN in close column should not crash."""

    def test_backtest_nan_close_prices(self):
        df = make_enhanced_df(n=20)
        # Inject NaN into some close prices
        df.loc[df.index[5], "close"] = np.nan
        df.loc[df.index[10], "close"] = np.nan

        dna = make_dna()
        engine = make_engine()

        # vectorbt may handle NaN by producing NaN equity or raise;
        # we just verify it does not crash with an unhandled exception
        # and produces some result
        result = engine.run(dna, df)
        assert isinstance(result.total_return, float) or result.total_return is not None


# ============================================================================
# validation/engine.py boundary tests
# ============================================================================


class TestValidateEmptyDataframe:
    """validate_hypothesis with a DataFrame that becomes empty after date filtering."""

    @patch("core.validation.engine.compute_all_indicators")
    @patch("core.validation.engine.load_parquet")
    def test_validate_empty_dataframe(self, mock_load, mock_compute):
        # Return a DataFrame that will be filtered to empty by the date range
        idx = pd.date_range("2023-01-01", periods=10, freq="4h")
        df = pd.DataFrame(
            {
                "open": np.full(10, 100.0),
                "high": np.full(10, 101.0),
                "low": np.full(10, 99.0),
                "close": np.full(10, 100.0),
                "volume": np.full(10, 1000.0),
            },
            index=idx,
        )
        mock_load.return_value = df
        mock_compute.return_value = df

        from core.validation.engine import validate_hypothesis

        # Date range is entirely after the data, so df becomes empty after filtering
        result = validate_hypothesis(
            pair="BTCUSDT",
            timeframe="4h",
            start="2025-01-01",  # after all data
            end="2025-12-31",
            when_conditions=[{"subject": "close", "action": "gt", "target": "90"}],
            then_conditions=[{"action": "rise", "target": "1"}],
            data_dir="/tmp/nonexistent",
        )

        assert result.total_count == 0
        assert result.match_rate == 0.0
        assert result.match_count == 0


class TestValidateZeroTriggerPrice:
    """When trigger_price is 0, change_pct should handle without division error."""

    @patch("core.validation.engine.compute_all_indicators")
    @patch("core.validation.engine.load_parquet")
    def test_validate_zero_trigger_price(self, mock_load, mock_compute):
        # Create data where a WHEN trigger fires on a bar with close=0
        idx = pd.date_range("2024-01-01", periods=20, freq="4h")
        close_values = np.full(20, 100.0)
        close_values[5] = 0.0  # bar that will trigger, with close=0
        df = pd.DataFrame(
            {
                "open": np.full(20, 100.0),
                "high": np.full(20, 101.0),
                "low": np.full(20, 99.0),
                "close": close_values,
                "volume": np.full(20, 1000.0),
            },
            index=idx,
        )
        mock_load.return_value = df
        mock_compute.return_value = df

        from core.validation.engine import validate_hypothesis

        # WHEN close gt 90 -> True on all bars except bar 5 where close=0
        # But we need WHEN to fire on bar 5. Use lt 1 instead.
        # Actually, to trigger on bar 5 where close=0, use "lt" with target "1"
        result = validate_hypothesis(
            pair="BTCUSDT",
            timeframe="4h",
            start="2024-01-01",
            end="2024-12-31",
            when_conditions=[{"subject": "close", "action": "lt", "target": "1"}],
            then_conditions=[{"action": "rise", "target": "1"}],
            data_dir="/tmp/nonexistent",
        )

        # The function should complete without ZeroDivisionError.
        # change_pct = ((final_price - 0) / 0) * 100 -> inf or nan
        # It should either produce a valid result or handle gracefully.
        # The key assertion: no unhandled exception
        assert isinstance(result, type(result))
        # With close=0 and division, change_pct may be inf or nan,
        # but the function should not crash.
        if result.total_count > 0:
            # If triggers were produced, verify change_pct values
            for trigger in result.triggers:
                # inf or nan values are acceptable as long as no crash
                assert isinstance(trigger.change_pct, float)
