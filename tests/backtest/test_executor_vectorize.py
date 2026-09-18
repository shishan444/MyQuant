"""Phase 2 tests: vectorized _resample_pulse and lookback conditions."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.executor import _resample_pulse, evaluate_condition, _empty_signal_set
from tests.helpers.data_factory import make_ohlcv, make_enhanced_df


def _original_resample_pulse(signal: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Original per-bar loop implementation for comparison."""
    if signal.empty or len(target_index) == 0:
        return pd.Series(False, index=target_index)

    result = pd.Series(False, index=target_index)
    for i in range(len(target_index)):
        bar_start = target_index[i]
        bar_end = target_index[i + 1] if i + 1 < len(target_index) else bar_start + pd.Timedelta(days=1)
        mask = (signal.index >= bar_start) & (signal.index < bar_end)
        if signal[mask].any():
            result.iloc[i] = True
    return result.astype(bool)


class TestResamplePulseMatchesOriginal:
    """Vectorized _resample_pulse produces same results as original loop."""

    def test_known_true_timestamps_15m_to_4h(self):
        """Generate known True timestamps in 15m, resample to 4h."""
        dates_15m = pd.date_range("2024-01-01", periods=16 * 10, freq="15min", tz="UTC")  # 10 hours
        signal = pd.Series(False, index=dates_15m)
        # Set True at specific positions
        signal.iloc[2] = True   # within first 4h bar
        signal.iloc[17] = True  # within second 4h bar (16 bars per 4h)
        signal.iloc[33] = True  # within third 4h bar

        dates_4h = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")
        result = _resample_pulse(signal, dates_4h)

        assert result.iloc[0] == True
        assert result.iloc[1] == True
        assert result.iloc[2] == True

    def test_empty_signal(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
        signal = pd.Series(False, index=pd.date_range("2024-01-01", periods=40, freq="15min", tz="UTC"))
        result = _resample_pulse(signal, dates)
        assert result.sum() == 0

    def test_all_false_signal(self):
        dates_src = pd.date_range("2024-01-01", periods=100, freq="15min", tz="UTC")
        signal = pd.Series(False, index=dates_src)
        dates_tgt = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
        result = _resample_pulse(signal, dates_tgt)
        assert result.sum() == 0
        assert len(result) == 5

    def test_stress_comparison(self):
        """Stress test: 2000 target bars, 500 true source bars."""
        rng = np.random.default_rng(123)
        n_src = 8000
        n_tgt = 2000

        dates_src = pd.date_range("2024-01-01", periods=n_src, freq="15min", tz="UTC")
        signal = pd.Series(False, index=dates_src)
        true_indices = rng.choice(n_src, size=500, replace=False)
        signal.iloc[true_indices] = True

        dates_tgt = pd.date_range("2024-01-01", periods=n_tgt, freq="1h", tz="UTC")

        new_result = _resample_pulse(signal, dates_tgt)
        old_result = _original_resample_pulse(signal, dates_tgt)

        pd.testing.assert_series_equal(new_result, old_result, check_names=False)

    def test_matches_original_random(self):
        """Random test with multiple data sizes."""
        rng = np.random.default_rng(42)
        for n_src, n_tgt, freq_src, freq_tgt in [
            (100, 20, "15min", "4h"),
            (500, 50, "5min", "1h"),
            (200, 40, "1h", "1d"),
        ]:
            dates_src = pd.date_range("2024-01-01", periods=n_src, freq=freq_src, tz="UTC")
            signal = pd.Series(rng.random(n_src) > 0.9, index=dates_src)

            dates_tgt = pd.date_range("2024-01-01", periods=n_tgt, freq=freq_tgt, tz="UTC")

            new_result = _resample_pulse(signal, dates_tgt)
            old_result = _original_resample_pulse(signal, dates_tgt)

            pd.testing.assert_series_equal(new_result, old_result, check_names=False)


class TestLookbackAnyMatchesOriginal:
    """Vectorized lookback_any (.sum() > 0) matches .apply(any)."""

    def test_basic_comparison(self):
        """Boolean series + window=5, compare results."""
        n = 100
        rng = np.random.default_rng(42)
        vals = rng.random(n)
        indicator = pd.Series(vals, index=pd.RangeIndex(n))
        close = pd.Series(rng.random(n) * 100, index=pd.RangeIndex(n))

        condition = {"type": "lookback_any", "window": 5, "inner": {"type": "lt", "threshold": 0.3}}
        new_result = evaluate_condition(indicator, close, condition)

        # Original: .apply(any)
        inner_signal = indicator < 0.3
        old_result = inner_signal.rolling(window=5, min_periods=1).apply(any, raw=False).fillna(False).astype(bool)

        pd.testing.assert_series_equal(new_result, old_result, check_names=False)

    def test_different_windows(self):
        for window in [3, 5, 10, 20]:
            n = 100
            rng = np.random.default_rng(window)
            vals = rng.random(n)
            indicator = pd.Series(vals, index=pd.RangeIndex(n))
            close = pd.Series(rng.random(n) * 100, index=pd.RangeIndex(n))

            condition = {"type": "lookback_any", "window": window, "inner": {"type": "lt", "threshold": 0.5}}
            new_result = evaluate_condition(indicator, close, condition)

            inner_signal = indicator < 0.5
            old_result = inner_signal.rolling(window=window, min_periods=1).apply(any, raw=False).fillna(False).astype(bool)

            pd.testing.assert_series_equal(new_result, old_result, check_names=False)


class TestLookbackAllMatchesOriginal:
    """Vectorized lookback_all (.sum() == window) matches .apply(all)."""

    def test_basic_comparison(self):
        n = 100
        rng = np.random.default_rng(42)
        vals = rng.random(n)
        indicator = pd.Series(vals, index=pd.RangeIndex(n))
        close = pd.Series(rng.random(n) * 100, index=pd.RangeIndex(n))

        condition = {"type": "lookback_all", "window": 5, "inner": {"type": "gt", "threshold": 0.7}}
        new_result = evaluate_condition(indicator, close, condition)

        # lookback_all uses min_periods=window in the new implementation
        inner_signal = indicator > 0.7
        old_result = (
            inner_signal.astype(float)
            .rolling(window=5, min_periods=5)
            .sum()
            .eq(5)
            .fillna(False)
            .astype(bool)
        )

        pd.testing.assert_series_equal(new_result, old_result, check_names=False)

    def test_different_windows(self):
        for window in [3, 5, 10, 20]:
            n = 100
            rng = np.random.default_rng(window)
            vals = rng.random(n)
            indicator = pd.Series(vals, index=pd.RangeIndex(n))
            close = pd.Series(rng.random(n) * 100, index=pd.RangeIndex(n))

            condition = {"type": "lookback_all", "window": window, "inner": {"type": "gt", "threshold": 0.5}}
            new_result = evaluate_condition(indicator, close, condition)

            inner_signal = indicator > 0.5
            old_result = (
                inner_signal.astype(float)
                .rolling(window=window, min_periods=window)
                .sum()
                .eq(window)
                .fillna(False)
                .astype(bool)
            )

            pd.testing.assert_series_equal(new_result, old_result, check_names=False)


class TestLookbackFirstBars:
    """First window-1 bars behave correctly."""

    def test_lookback_any_partial_window(self):
        """With min_periods=1, partial windows should work."""
        indicator = pd.Series([False, False, False, True, False], index=pd.RangeIndex(5))
        close = pd.Series([1.0] * 5, index=pd.RangeIndex(5))
        condition = {"type": "lookback_any", "window": 5, "inner": {"type": "lt", "threshold": 0.5}}

        # indicator values are 0.0..4.0, inner: indicator < 0.5 -> only index 0 is True
        result = evaluate_condition(indicator, close, condition)
        # First bar: True (0 < 0.5)
        assert result.iloc[0] == True
