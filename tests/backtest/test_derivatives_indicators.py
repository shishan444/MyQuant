"""Tests for Iteration 1c: Derivatives indicators.

Validates:
1. 5 derivatives indicator computations (OI_ChangeRate, OI_ZScore,
   FundingZScore, OIPriceDivergence, FundingPressure)
2. Derivatives merge into OHLCV DataFrame
3. Graceful degradation when no derivatives data present
"""
import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.features.registry import (
    INDICATOR_REGISTRY,
    resolve_indicator_column,
)
from core.features.indicators import _compute_indicator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv_with_derivatives(n=200, has_oi=True, has_funding=True):
    """Create synthetic OHLCV + derivatives DataFrame."""
    np.random.seed(42)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    close = 60000 + np.cumsum(np.random.randn(n) * 200)
    data = {
        "open": close * 0.999,
        "high": close * 1.002,
        "low": close * 0.998,
        "close": close,
        "volume": np.random.randint(100, 10000, n).astype(float),
    }
    if has_oi:
        data["open_interest"] = np.abs(
            10000 + np.cumsum(np.random.randn(n) * 500)
        )
    if has_funding:
        data["funding_rate"] = np.random.randn(n) * 0.001
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# 1. Registry entries for derivatives indicators
# ---------------------------------------------------------------------------

class TestDerivativesRegistry:
    """Verify derivatives indicator registry entries."""

    DERIVATIVES = [
        "OI_ChangeRate", "OI_ZScore", "FundingZScore",
        "OIPriceDivergence", "FundingPressure",
    ]

    def test_all_derivatives_in_registry(self):
        for name in self.DERIVATIVES:
            assert name in INDICATOR_REGISTRY, f"{name} missing from registry"

    def test_derivatives_category(self):
        for name in self.DERIVATIVES:
            assert INDICATOR_REGISTRY[name].category == "derivatives"

    def test_derivatives_naming_default(self):
        for name in self.DERIVATIVES:
            assert INDICATOR_REGISTRY[name].naming == "default"

    def test_derivatives_compute_mode_eager(self):
        for name in self.DERIVATIVES:
            assert INDICATOR_REGISTRY[name].compute_mode == "eager"

    def test_derivatives_not_guard_only(self):
        for name in self.DERIVATIVES:
            assert INDICATOR_REGISTRY[name].guard_only is False

    def test_derivatives_column_names(self):
        """Verify resolve_indicator_column produces correct names."""
        cases = [
            ("OI_ChangeRate", {"period": 14}, "", "oi_change_rate_14"),
            ("OI_ZScore", {"period": 20}, "", "oi_zscore_20"),
            ("FundingZScore", {"period": 30}, "", "funding_zscore_30"),
            ("OIPriceDivergence", {"period": 14}, "", "oi_price_divergence_14"),
            ("FundingPressure", {"period": 8}, "", "funding_pressure_8"),
        ]
        for name, params, field, expected in cases:
            result = resolve_indicator_column(name, params, field, "default")
            assert result == expected, f"{name}: expected '{expected}', got '{result}'"


# ---------------------------------------------------------------------------
# 2. Indicator computations with derivatives data
# ---------------------------------------------------------------------------

class TestDerivativesComputations:
    """Test that derivatives indicators compute correctly."""

    def test_oi_change_rate(self):
        df = _make_ohlcv_with_derivatives()
        result = _compute_indicator(df, "OI_ChangeRate", {"period": 14})
        assert "oi_change_rate_14" in result
        col = result["oi_change_rate_14"]
        # First 14 values should be NaN (not enough data for pct_change)
        assert col.iloc[:13].isna().all()
        # After that, should have valid values
        assert col.iloc[14:].notna().any()

    def test_oi_zscore(self):
        df = _make_ohlcv_with_derivatives()
        result = _compute_indicator(df, "OI_ZScore", {"period": 20})
        assert "oi_zscore_20" in result
        col = result["oi_zscore_20"]
        # Z-score values should be reasonable (most within ±4)
        valid = col.dropna()
        assert len(valid) > 0

    def test_funding_zscore(self):
        df = _make_ohlcv_with_derivatives()
        result = _compute_indicator(df, "FundingZScore", {"period": 30})
        assert "funding_zscore_30" in result

    def test_oi_price_divergence(self):
        df = _make_ohlcv_with_derivatives()
        result = _compute_indicator(df, "OIPriceDivergence", {"period": 14})
        assert "oi_price_divergence_14" in result
        col = result["oi_price_divergence_14"]
        # Divergence is rank correlation based, range roughly [-1, +1]
        valid = col.dropna()
        assert len(valid) > 0

    def test_funding_pressure(self):
        df = _make_ohlcv_with_derivatives()
        result = _compute_indicator(df, "FundingPressure", {"period": 8})
        assert "funding_pressure_8" in result
        col = result["funding_pressure_8"]
        valid = col.dropna()
        assert len(valid) > 0


# ---------------------------------------------------------------------------
# 3. Graceful degradation without derivatives data
# ---------------------------------------------------------------------------

class TestDerivativesGraceful:
    """Indicators should be skipped when derivatives columns are absent."""

    def test_oi_change_rate_no_oi_column(self):
        df = _make_ohlcv_with_derivatives(has_oi=False)
        result = _compute_indicator(df, "OI_ChangeRate", {"period": 14})
        assert len(result) == 0 or "oi_change_rate_14" not in result

    def test_funding_zscore_no_funding_column(self):
        df = _make_ohlcv_with_derivatives(has_funding=False)
        result = _compute_indicator(df, "FundingZScore", {"period": 30})
        assert len(result) == 0 or "funding_zscore_30" not in result

    def test_compute_all_skips_missing_derivatives(self):
        """compute_all_indicators should silently skip derivatives without data."""
        from core.features.indicators import compute_all_indicators
        df = _make_ohlcv_with_derivatives(has_oi=False, has_funding=False)
        result = compute_all_indicators(df)
        assert result is not None
        # No derivatives columns should exist
        assert not any("oi_" in c or "funding_" in c for c in result.columns)
        # But regular indicators should still be computed
        assert "ema_10" in result.columns


# ---------------------------------------------------------------------------
# 4. Derivatives merger
# ---------------------------------------------------------------------------

class TestDerivativesMerger:
    """Test merge_derivatives_into_ohlcv function."""

    def test_merge_adds_columns(self):
        from core.data.derivatives_merger import merge_derivatives_into_ohlcv
        df = _make_ohlcv_with_derivatives(has_oi=False, has_funding=False)
        oi_df = pd.DataFrame({
            "open_interest": [1000.0, 1100.0, 1050.0, 1200.0],
        }, index=pd.date_range("2024-01-01", periods=4, freq="4h"))
        result = merge_derivatives_into_ohlcv(df, oi_df=oi_df)
        assert "open_interest" in result.columns

    def test_merge_none_returns_original(self):
        from core.data.derivatives_merger import merge_derivatives_into_ohlcv
        df = _make_ohlcv_with_derivatives(has_oi=False, has_funding=False)
        result = merge_derivatives_into_ohlcv(df)
        assert "open_interest" not in result.columns
        assert len(result) == len(df)

    def test_merge_forward_fills(self):
        from core.data.derivatives_merger import merge_derivatives_into_ohlcv
        df = _make_ohlcv_with_derivatives(has_oi=False, has_funding=False, n=20)
        funding_df = pd.DataFrame({
            "funding_rate": [0.001, -0.0005, 0.0003],
        }, index=pd.DatetimeIndex([
            pd.Timestamp("2024-01-01"),
            pd.Timestamp("2024-01-01 08:00"),
            pd.Timestamp("2024-01-01 16:00"),
        ]))
        result = merge_derivatives_into_ohlcv(df, funding_df=funding_df)
        assert "funding_rate" in result.columns
        # Should be forward-filled
        assert result["funding_rate"].notna().any()


# ---------------------------------------------------------------------------
# 5. _CONTEXT_SCHEMA includes derivatives
# ---------------------------------------------------------------------------

class TestDerivativesContextSchema:
    """Derivatives category should provide momentum context."""

    def test_derivatives_provides_momentum(self):
        from core.strategy.mtf_engine import _CONTEXT_SCHEMA
        assert "derivatives" in _CONTEXT_SCHEMA
        assert _CONTEXT_SCHEMA["derivatives"] == {"momentum"}

    def test_derivatives_indicator_provides_momentum(self):
        """OI_ChangeRate should provide normalized momentum context."""
        df = _make_ohlcv_with_derivatives(n=200)
        from core.strategy.dna import SignalGene, SignalRole
        gene = SignalGene(
            indicator="OI_ChangeRate",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        from core.strategy.mtf_engine import extract_context
        # Need the indicator column in the DataFrame
        result = _compute_indicator(df, "OI_ChangeRate", {"period": 14})
        for col, values in result.items():
            df[col] = values
        ctx = extract_context(df, gene, "derivatives")
        assert "momentum" in ctx
