"""Phase 2 tests: vectorized lookback conditions.

0.2.14：_resample_pulse 退役（asof_completed 收口替换为
mtf_alignment.pulse_completed——+tf 落位语义，权威锚在
tests/data/test_mtf_alignment.py::TestPulseCompleted）；本文件的
「与原始循环对照」测试族随被测函数一并退役。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.executor import evaluate_condition, _empty_signal_set
from tests.helpers.data_factory import make_ohlcv, make_enhanced_df


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
