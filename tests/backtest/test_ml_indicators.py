"""Tests for Iteration 2: ML indicators (FractalEntropy + MultifactorOsc).

Validates: registry entries, computation, output ranges, lazy compute mode,
NaN handling, and MTF context integration.
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.features.registry import (
    INDICATOR_REGISTRY,
    resolve_indicator_column,
)
from core.features.indicators import _compute_indicator, compute_all_indicators


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with enough bars for ML indicators."""
    rng = np.random.RandomState(seed)
    close = 100 + np.cumsum(rng.randn(n) * 0.5)
    return pd.DataFrame({
        "open": close + rng.randn(n) * 0.1,
        "high": close + np.abs(rng.randn(n) * 0.3),
        "low": close - np.abs(rng.randn(n) * 0.3),
        "close": close,
        "volume": rng.randint(100, 10000, n).astype(float),
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))


# ---------------------------------------------------------------------------
# 1. Registry entries
# ---------------------------------------------------------------------------

class TestMLRegistry:
    """Validate ML indicator registry entries."""

    @pytest.mark.parametrize("name", ["FractalEntropy", "MultifactorOsc"])
    def test_registry_entry_exists(self, name):
        assert name in INDICATOR_REGISTRY

    @pytest.mark.parametrize("name", ["FractalEntropy", "MultifactorOsc"])
    def test_category_is_ml(self, name):
        assert INDICATOR_REGISTRY[name].category == "ml"

    @pytest.mark.parametrize("name", ["FractalEntropy", "MultifactorOsc"])
    def test_compute_mode_is_lazy(self, name):
        assert INDICATOR_REGISTRY[name].compute_mode == "lazy"

    @pytest.mark.parametrize("name", ["FractalEntropy", "MultifactorOsc"])
    def test_naming_is_not_default(self, name):
        """ML indicators use special naming modes, not 'default'."""
        assert INDICATOR_REGISTRY[name].naming != "default"

    def test_fractal_entropy_naming(self):
        assert INDICATOR_REGISTRY["FractalEntropy"].naming == "mfe"

    def test_multifactor_osc_naming(self):
        assert INDICATOR_REGISTRY["MultifactorOsc"].naming == "mf_osc"

    def test_fractal_entropy_params(self):
        reg = INDICATOR_REGISTRY["FractalEntropy"]
        assert "bins" in reg.params
        assert "lookback" in reg.params

    def test_multifactor_osc_params(self):
        reg = INDICATOR_REGISTRY["MultifactorOsc"]
        assert "lookback" in reg.params


# ---------------------------------------------------------------------------
# 2. Column name resolution
# ---------------------------------------------------------------------------

class TestMLColumnResolution:
    """Validate column name generation for ML indicators."""

    def test_fractal_entropy_column(self):
        col = resolve_indicator_column(
            "FractalEntropy", {"bins": 10, "lookback": 100},
        )
        assert col == "mfe_score_10_100"

    def test_fractal_entropy_column_different_params(self):
        col = resolve_indicator_column(
            "FractalEntropy", {"bins": 20, "lookback": 50},
        )
        assert col == "mfe_score_20_50"

    def test_multifactor_osc_column(self):
        col = resolve_indicator_column(
            "MultifactorOsc", {"lookback": 20},
        )
        assert col == "mf_osc_20"


# ---------------------------------------------------------------------------
# 3. FractalEntropy computation
# ---------------------------------------------------------------------------

class TestFractalEntropyComputation:
    """Validate FractalEntropy indicator computation."""

    def test_output_column_name(self):
        df = _make_ohlcv(300)
        new_cols = _compute_indicator(df, "FractalEntropy", {"bins": 10, "lookback": 100})
        assert "mfe_score_10_100" in new_cols

    def test_output_range_0_to_1(self):
        df = _make_ohlcv(300)
        new_cols = _compute_indicator(df, "FractalEntropy", {"bins": 10, "lookback": 100})
        series = new_cols["mfe_score_10_100"].dropna()
        assert (series >= 0).all() and (series <= 1).all()

    def test_initial_nan_warmup(self):
        df = _make_ohlcv(300)
        new_cols = _compute_indicator(df, "FractalEntropy", {"bins": 10, "lookback": 100})
        # First lookback-1 values should be NaN
        assert new_cols["mfe_score_10_100"].iloc[:99].isna().all()

    def test_later_values_not_nan(self):
        df = _make_ohlcv(300)
        new_cols = _compute_indicator(df, "FractalEntropy", {"bins": 10, "lookback": 100})
        # Values after warmup should be non-NaN
        non_nan = new_cols["mfe_score_10_100"].iloc[100:]
        assert non_nan.notna().sum() > 100

    def test_entropy_sensitivity(self):
        """Different data patterns should produce different entropy values."""
        rng = np.random.RandomState(42)
        n = 300

        # Uniform random returns → high entropy (near max)
        uniform_returns = rng.uniform(-1, 1, n)
        uniform_close = pd.Series(100 * np.cumprod(1 + uniform_returns * 0.001))

        # Concentrated returns (mostly zero with occasional spikes) → lower entropy
        concentrated_returns = np.zeros(n)
        concentrated_returns[::20] = rng.choice([-0.01, 0.01], size=n // 20)
        concentrated_close = pd.Series(100 * np.cumprod(1 + concentrated_returns))

        uniform_cols = _compute_indicator(
            pd.DataFrame({"close": uniform_close}),
            "FractalEntropy", {"bins": 10, "lookback": 100},
        )
        concentrated_cols = _compute_indicator(
            pd.DataFrame({"close": concentrated_close}),
            "FractalEntropy", {"bins": 10, "lookback": 100},
        )

        uniform_entropy = uniform_cols["mfe_score_10_100"].dropna().mean()
        concentrated_entropy = concentrated_cols["mfe_score_10_100"].dropna().mean()

        # Both should be valid
        assert 0 <= uniform_entropy <= 1
        assert 0 <= concentrated_entropy <= 1
        # They should be different (entropy is sensitive to distribution shape)
        assert uniform_entropy != concentrated_entropy


# ---------------------------------------------------------------------------
# 4. MultifactorOsc computation
# ---------------------------------------------------------------------------

class TestMultifactorOscComputation:
    """Validate MultifactorOsc indicator computation."""

    def _make_df_with_indicators(self, n: int = 500) -> pd.DataFrame:
        df = _make_ohlcv(n)
        df = compute_all_indicators(df, skip_lazy=True)
        return df

    def test_output_column_name(self):
        df = self._make_df_with_indicators()
        new_cols = _compute_indicator(df, "MultifactorOsc", {"lookback": 20})
        assert "mf_osc_20" in new_cols

    def test_output_range(self):
        df = self._make_df_with_indicators()
        new_cols = _compute_indicator(df, "MultifactorOsc", {"lookback": 20})
        series = new_cols["mf_osc_20"].dropna()
        assert (series >= -1).all() and (series <= 1).all()

    def test_requires_sub_indicators(self):
        """Without sub-indicator columns, should return NaN."""
        df = _make_ohlcv(200)  # No indicators computed
        new_cols = _compute_indicator(df, "MultifactorOsc", {"lookback": 20})
        series = new_cols.get("mf_osc_20")
        if series is not None:
            assert series.isna().all() or len(series.dropna()) < 10

    def test_graceful_with_partial_indicators(self):
        """Should still work if only some sub-indicators are present."""
        df = _make_ohlcv(300)
        # Only compute RSI and CCI (2 of 4 sub-indicators)
        from core.features.indicators import _compute_indicator as _compute
        for name, params in [("RSI", {"period": 14}), ("CCI", {"period": 20})]:
            cols = _compute(df, name, params)
            for col, vals in cols.items():
                df[col] = vals
        # MultifactorOsc should still produce output (using available indicators)
        new_cols = _compute_indicator(df, "MultifactorOsc", {"lookback": 20})
        assert "mf_osc_20" in new_cols
        assert new_cols["mf_osc_20"].notna().any()


# ---------------------------------------------------------------------------
# 5. Lazy compute mode
# ---------------------------------------------------------------------------

class TestLazyComputeMode:
    """Validate skip_lazy behavior in compute_all_indicators."""

    def test_skip_lazy_true_skips_ml(self):
        df = _make_ohlcv(300)
        result = compute_all_indicators(df, skip_lazy=True)
        assert "mfe_score_10_100" not in result.columns
        assert "mf_osc_20" not in result.columns

    def test_skip_lazy_false_includes_ml(self):
        df = _make_ohlcv(300)
        result = compute_all_indicators(df, skip_lazy=False)
        assert "mfe_score_10_100" in result.columns
        assert "mf_osc_20" in result.columns

    def test_skip_lazy_default_is_true(self):
        """Default behavior should skip lazy indicators."""
        df = _make_ohlcv(300)
        result = compute_all_indicators(df)
        assert "mfe_score_10_100" not in result.columns

    def test_skip_lazy_true_still_computes_eager(self):
        """Non-lazy indicators should still be computed."""
        df = _make_ohlcv(300)
        result = compute_all_indicators(df, skip_lazy=True)
        assert "rsi_14" in result.columns
        assert "ema_50" in result.columns

    def test_volume_profile_also_skipped_when_lazy(self):
        """VolumeProfile (compute_mode='lazy') should also be skipped."""
        df = _make_ohlcv(300)
        result = compute_all_indicators(df, skip_lazy=True)
        # VolumeProfile columns should not exist
        vp_cols = [c for c in result.columns if c.startswith("vp_")]
        assert len(vp_cols) == 0


# ---------------------------------------------------------------------------
# 6. MTF context integration
# ---------------------------------------------------------------------------

class TestMLContextIntegration:
    """Validate ML indicators in MTF context schema."""

    def test_ml_category_in_context_schema(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert "ml" in _CONTEXT_SCHEMA
        assert "momentum" in _CONTEXT_SCHEMA["ml"]

    def test_ml_indicator_provides_momentum(self):
        """ML indicators should provide momentum context when normalized."""
        from core.strategy.mtf_engine import extract_context
        df = _make_ohlcv(300)
        result = compute_all_indicators(df, skip_lazy=False)

        # Create a minimal signal gene for testing
        gene = type("Gene", (), {
            "indicator": "FractalEntropy",
            "params": {"bins": 10, "lookback": 100},
            "field_name": None,
            "condition": {"type": "gt"},
        })()
        ctx = extract_context(result, gene, "ml")
        assert "momentum" in ctx
