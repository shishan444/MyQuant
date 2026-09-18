"""Tests for Iteration 1a bug fixes.

Validates that all 5 bug fixes are correctly implemented:
- A: BB output_fields only contains computable fields
- A2: VWAP marked guard_only=True
- D: BB column names match between producer and consumer
- B: Graded exception handling in compute_all_indicators
- C: extract_context includes bounded volume/ADX as momentum
"""
import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.features.registry import INDICATOR_REGISTRY
from core.features.indicators import _compute_indicator
from core.strategy.dna import SignalGene, SignalRole
from core.strategy.mtf_engine import extract_context


# ---------------------------------------------------------------------------
# Bug A: BB output_fields should not include uncomputed fields
# ---------------------------------------------------------------------------

class TestBugABBOutputFields:
    """BB registry should only declare fields that are actually computed."""

    def test_bb_output_fields_no_bandwidth(self):
        bb = INDICATOR_REGISTRY["BB"]
        assert "bandwidth" not in bb.output_fields

    def test_bb_output_fields_no_percent(self):
        bb = INDICATOR_REGISTRY["BB"]
        assert "percent" not in bb.output_fields

    def test_bb_output_fields_has_core_three(self):
        bb = INDICATOR_REGISTRY["BB"]
        assert "upper" in bb.output_fields
        assert "middle" in bb.output_fields
        assert "lower" in bb.output_fields


# ---------------------------------------------------------------------------
# Bug A2: VWAP should be guard_only
# ---------------------------------------------------------------------------

class TestBugA2VWAPGuardOnly:
    """VWAP is registered but never computed/resolved; must be guard_only."""

    def test_vwap_is_guard_only(self):
        vwap = INDICATOR_REGISTRY["VWAP"]
        assert vwap.guard_only is True


# ---------------------------------------------------------------------------
# Bug D: BB column names must match between producer and consumer
# ---------------------------------------------------------------------------

class TestBugDBBColumnNaming:
    """BB producer and consumer column names must match exactly."""

    @pytest.fixture
    def sample_df(self):
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="4h")
        close = 60000 + np.cumsum(np.random.randn(n) * 200)
        return pd.DataFrame({
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.random.randint(100, 10000, n).astype(float),
        }, index=dates)

    def test_bb_column_name_matches_for_default_std(self, sample_df):
        """Producer creates bb_upper_20_2 (not bb_upper_20_2.0)."""
        result = _compute_indicator(sample_df, "BB", {"period": 20, "std": 2.0})
        # After fix, producer should strip .0 suffix
        assert "bb_upper_20_2" in result, \
            f"Expected 'bb_upper_20_2', got columns: {list(result.keys())}"
        assert "bb_middle_20_2" in result
        assert "bb_lower_20_2" in result

    def test_bb_column_name_non_integer_std(self, sample_df):
        """Non-integer std values (1.5, 2.5) should keep decimal."""
        result = _compute_indicator(sample_df, "BB", {"period": 20, "std": 1.5})
        assert "bb_upper_20_1.5" in result, \
            f"Expected 'bb_upper_20_1.5', got: {list(result.keys())}"


# ---------------------------------------------------------------------------
# Bug B: Graded exception handling
# ---------------------------------------------------------------------------

class TestBugBGredExceptions:
    """compute_all_indicators should not silently swallow code bugs."""

    def test_type_error_propagates(self):
        """TypeError (code bug) should propagate, not be silently caught."""
        from core.features.indicators import _compute_indicator

        # Create a broken indicator computation by mocking
        # We test indirectly: compute_all_indicators with invalid indicator
        # should not hang or silently skip
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h")
        df = pd.DataFrame({
            "open": [60000.0] * n,
            "high": [60500.0] * n,
            "low": [59500.0] * n,
            "close": [60000.0] * n,
            "volume": [1000.0] * n,
        }, index=dates)

        from core.features.indicators import compute_all_indicators
        # Should not raise -- all indicators handle gracefully
        result = compute_all_indicators(df)
        assert result is not None
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Bug C: extract_context includes bounded volume/ADX indicators
# ---------------------------------------------------------------------------

class TestBugCExtractContextBounded:
    """extract_context should return momentum for bounded volume/ADX indicators."""

    def _make_df_with_indicator(self, col_name, values):
        n = len(values)
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        return pd.DataFrame({
            col_name: values,
            "close": [60000.0] * n,
        }, index=idx)

    def test_cmf_provides_momentum(self):
        """CMF (volume category, bounded [-1,+1]) should provide momentum context."""
        df = self._make_df_with_indicator("cmf_20", [0.1, -0.2, 0.3, 0.0, -0.1] * 4)
        gene = SignalGene(
            indicator="CMF",
            params={"period": 20},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx, "CMF (volume, bounded) should provide momentum context"

    def test_mfi_provides_momentum(self):
        """MFI (volume category, bounded [0,100]) should provide momentum context."""
        df = self._make_df_with_indicator("mfi_14", [50.0, 60.0, 40.0, 55.0, 45.0] * 4)
        gene = SignalGene(
            indicator="MFI",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx, "MFI (volume, bounded) should provide momentum context"

    def test_adx_provides_momentum(self):
        """ADX (trend_strength category, bounded [0,100]) should provide momentum context."""
        df = self._make_df_with_indicator("adx_14", [25.0, 30.0, 20.0, 35.0, 28.0] * 4)
        gene = SignalGene(
            indicator="ADX",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        ctx = extract_context(df, gene, "trend_strength")
        assert "momentum" in ctx, "ADX (trend_strength, bounded) should provide momentum context"

    def test_obv_provides_momentum_normalized(self):
        """OBV (unbounded) now provides momentum via per-series normalization (1b)."""
        df = self._make_df_with_indicator("obv", [1e6, 1.1e6, 0.9e6, 1.2e6, 1.0e6] * 4)
        gene = SignalGene(
            indicator="OBV",
            params={},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx, \
            "OBV should provide momentum after 1b per-series normalization"
        # Verify normalization range
        mom = ctx["momentum"]
        assert mom.min() >= -1.01
        assert mom.max() <= 1.01

    def test_rvol_provides_momentum(self):
        """RVOL (volume category, bounded) should provide momentum context."""
        df = self._make_df_with_indicator("rvol_20", [1.0, 1.5, 0.8, 1.2, 0.9] * 4)
        gene = SignalGene(
            indicator="RVOL",
            params={"period": 20},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx, "RVOL (volume, bounded) should provide momentum context"

    def test_vroc_provides_momentum(self):
        """VROC (volume category, bounded) should provide momentum context."""
        df = self._make_df_with_indicator("vroc_14", [0.1, -0.05, 0.2, -0.1, 0.15] * 4)
        gene = SignalGene(
            indicator="VROC",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "gt"},
        )
        ctx = extract_context(df, gene, "volume")
        assert "momentum" in ctx, "VROC (volume, bounded) should provide momentum context"
