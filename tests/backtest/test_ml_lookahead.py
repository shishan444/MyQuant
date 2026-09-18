"""Lookahead independence tests for ML indicators.

Verifies that ML indicators do not use future data (no lookahead bias).
Each indicator's value at bar i should depend only on data up to bar i.
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.ml_indicators import compute_fractal_entropy, compute_multifactor_osc


def _make_ohlcv(n=500):
    """Create synthetic OHLCV DataFrame with sub-indicators."""
    rng = np.random.default_rng(42)
    close = 60000 + rng.standard_normal(n).cumsum() * 100
    return pd.DataFrame(
        {
            "open": close + rng.standard_normal(n),
            "high": close + abs(rng.standard_normal(n)),
            "low": close - abs(rng.standard_normal(n)),
            "close": close,
            "volume": rng.uniform(100, 10000, n),
            "rsi_14": rng.uniform(20, 80, n),
            "cci_20": rng.standard_normal(n) * 50,
            "mfi_14": rng.uniform(20, 80, n),
            "stoch_k_14_3": rng.uniform(10, 90, n),
        },
        index=pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"),
    )


class TestFractalEntropyLookahead:
    """Verify FractalEntropy has no lookahead bias."""

    def test_value_at_bar_i_unchanged_by_future_data(self):
        """Modifying data after bar i should not change indicator value at bar i."""
        df = _make_ohlcv(500)
        result_full = compute_fractal_entropy(df["close"], bins=10, lookback=100)

        # Modify last 100 bars (far future relative to bar 250)
        df_modified = df.copy()
        df_modified.iloc[350:, :] = 0  # corrupt future data
        result_modified = compute_fractal_entropy(df_modified["close"], bins=10, lookback=100)

        # Values at bar 250 should be identical (future data not used)
        val_full = result_full.iloc[250]
        val_modified = result_modified.iloc[250]
        if not (np.isnan(val_full) or np.isnan(val_modified)):
            assert val_full == pytest.approx(val_modified, rel=1e-10)

    def test_rolling_window_property(self):
        """Value at bar i should depend only on the lookback window."""
        df = _make_ohlcv(300)
        lookback = 50
        result = compute_fractal_entropy(df["close"], bins=10, lookback=lookback)

        # Compute using only the window [i-lookback+1 : i+1] of RETURNS
        i = 200
        returns = df["close"].pct_change()
        window_data = returns.iloc[max(0, i - lookback + 1) : i + 1].dropna().values
        hist, _ = np.histogram(window_data, bins=10)
        prob = hist / hist.sum()
        prob = prob[prob > 0]
        entropy = -np.sum(prob * np.log2(prob))
        max_entropy = np.log2(10)
        expected = entropy / max_entropy

        val = result.iloc[i]
        if not np.isnan(val):
            assert val == pytest.approx(expected, rel=1e-6)


class TestMultifactorOscLookahead:
    """Verify MultifactorOsc has no lookahead bias."""

    def test_value_at_bar_i_unchanged_by_future_data(self):
        df = _make_ohlcv(500)
        result_full = compute_multifactor_osc(df, lookback=20)

        df_modified = df.copy()
        df_modified.iloc[400:, :] = 0
        result_modified = compute_multifactor_osc(df_modified, lookback=20)

        val_full = result_full.iloc[300]
        val_modified = result_modified.iloc[300]
        if not (np.isnan(val_full) or np.isnan(val_modified)):
            assert val_full == pytest.approx(val_modified, rel=1e-10)

    def test_output_within_range(self):
        """Output should be bounded in [-1, 1]."""
        df = _make_ohlcv(500)
        result = compute_multifactor_osc(df, lookback=20)
        valid = result.dropna()
        assert valid.min() >= -1.0 - 1e-10
        assert valid.max() <= 1.0 + 1e-10
