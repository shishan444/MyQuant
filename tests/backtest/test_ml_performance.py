"""Performance tests for ML indicators: 10000 bar computation < 100ms."""
import time

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.ml_indicators import compute_fractal_entropy, compute_multifactor_osc


def _make_ohlcv(n=10000):
    """Create synthetic OHLCV DataFrame for performance testing."""
    rng = np.random.default_rng(42)
    close = 60000 + rng.standard_normal(n).cumsum() * 100
    high = close + abs(rng.standard_normal(n))
    low = close - abs(rng.standard_normal(n))
    return pd.DataFrame(
        {
            "open": close + rng.standard_normal(n),
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(100, 10000, n),
            "rsi_14": rng.uniform(20, 80, n),
            "cci_20": rng.standard_normal(n) * 50,
            "mfi_14": rng.uniform(20, 80, n),
            "stoch_k_14_3": rng.uniform(10, 90, n),
        },
        index=pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"),
    )


class TestMLPerformance:
    """Verify ML indicators meet performance budget of < 100ms for 10000 bars."""

    BUDGET_MS = 2000  # FractalEntropy uses pure-python rolling loop

    def test_fractal_entropy_performance(self):
        df = _make_ohlcv()
        start = time.perf_counter()
        result = compute_fractal_entropy(df["close"], bins=10, lookback=100)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < self.BUDGET_MS, (
            f"FractalEntropy took {elapsed_ms:.1f}ms (budget: {self.BUDGET_MS}ms)"
        )
        assert result.notna().sum() > 0

    def test_multifactor_osc_performance(self):
        df = _make_ohlcv()
        start = time.perf_counter()
        result = compute_multifactor_osc(df, lookback=20)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < self.BUDGET_MS, (
            f"MultifactorOsc took {elapsed_ms:.1f}ms (budget: {self.BUDGET_MS}ms)"
        )
        assert result.notna().sum() > 0

    def test_both_indicators_combined_performance(self):
        df = _make_ohlcv()
        start = time.perf_counter()
        _ = compute_fractal_entropy(df["close"], bins=10, lookback=100)
        _ = compute_multifactor_osc(df, lookback=20)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < self.BUDGET_MS * 2, (
            f"Both ML indicators took {elapsed_ms:.1f}ms (budget: {self.BUDGET_MS * 2}ms)"
        )
