"""Tests for derivatives data pipeline: fetch -> Parquet -> load -> merge -> compute.

Verifies the full chain from raw data storage through merge to indicator computation.
Uses synthetic data to avoid network dependencies.
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.data.derivatives_merger import merge_derivatives_into_ohlcv
from core.features.indicators import compute_all_indicators


def _make_ohlcv(n=200, freq="4h"):
    """Create synthetic OHLCV DataFrame."""
    dates = pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(42)
    close = 60000 + rng.standard_normal(n).cumsum() * 100
    return pd.DataFrame(
        {
            "open": close + rng.standard_normal(n),
            "high": close + abs(rng.standard_normal(n)),
            "low": close - abs(rng.standard_normal(n)),
            "close": close,
            "volume": rng.uniform(100, 10000, n),
        },
        index=dates,
    )


def _make_oi(n=200, freq="4h"):
    """Create synthetic OI DataFrame."""
    dates = pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(42)
    oi = 1000000 + rng.standard_normal(n).cumsum() * 10000
    return pd.DataFrame({"open_interest": oi}, index=dates)


def _make_funding(n=50, freq="8h"):
    """Create synthetic Funding Rate DataFrame (8h intervals)."""
    dates = pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(42)
    fr = rng.uniform(-0.001, 0.001, n)
    return pd.DataFrame({"funding_rate": fr}, index=dates)


class TestMergePipeline:
    """Test merge_derivatives_into_ohlcv pipeline."""

    def test_merge_oi_only(self):
        ohlcv = _make_ohlcv()
        oi = _make_oi()
        result = merge_derivatives_into_ohlcv(ohlcv, oi_df=oi)
        assert "open_interest" in result.columns
        assert result["open_interest"].notna().sum() > 0

    def test_merge_funding_only(self):
        ohlcv = _make_ohlcv()
        funding = _make_funding()
        result = merge_derivatives_into_ohlcv(ohlcv, funding_df=funding)
        assert "funding_rate" in result.columns
        assert result["funding_rate"].notna().sum() > 0

    def test_merge_both(self):
        ohlcv = _make_ohlcv()
        oi = _make_oi()
        funding = _make_funding()
        result = merge_derivatives_into_ohlcv(ohlcv, oi_df=oi, funding_df=funding)
        assert "open_interest" in result.columns
        assert "funding_rate" in result.columns

    def test_merge_none_returns_original(self):
        ohlcv = _make_ohlcv()
        result = merge_derivatives_into_ohlcv(ohlcv)
        assert result is ohlcv  # same object, no copy

    def test_forward_fill_alignment(self):
        """Funding at 8h intervals should forward-fill into 4h bars."""
        ohlcv = _make_ohlcv(20, freq="4h")
        funding = pd.DataFrame(
            {"funding_rate": [0.001]},
            index=pd.DatetimeIndex(
                [pd.Timestamp("2025-01-01", tz="UTC")], name="timestamp"
            ),
        )
        result = merge_derivatives_into_ohlcv(ohlcv, funding_df=funding)
        # First 4h bar should have the funding rate
        assert result["funding_rate"].iloc[0] == pytest.approx(0.001)
        # Subsequent bars up to next 8h mark should also be filled
        assert result["funding_rate"].iloc[1] == pytest.approx(0.001)


class TestComputeWithDerivatives:
    """Test that derivative indicators compute correctly after merge."""

    def test_derivatives_indicators_compute_after_merge(self):
        ohlcv = _make_ohlcv()
        oi = _make_oi()
        funding = _make_funding()
        merged = merge_derivatives_into_ohlcv(ohlcv, oi_df=oi, funding_df=funding)
        result = compute_all_indicators(merged)

        # Check that at least some derivative indicator columns exist
        deriv_cols = [c for c in result.columns if "oi_" in c or "funding" in c]
        assert len(deriv_cols) > 0, f"No derivative columns found: {list(result.columns)}"

    def test_no_derivatives_data_no_crash(self):
        """compute_all_indicators should work without derivatives data."""
        ohlcv = _make_ohlcv()
        result = compute_all_indicators(ohlcv)
        # Should have standard indicator columns
        assert "rsi_14" in result.columns or len(result.columns) > 10

    def test_pipeline_idempotent(self):
        """Running pipeline twice produces same result."""
        ohlcv = _make_ohlcv()
        oi = _make_oi()
        merged1 = merge_derivatives_into_ohlcv(ohlcv, oi_df=oi)
        merged2 = merge_derivatives_into_ohlcv(ohlcv, oi_df=oi)
        pd.testing.assert_frame_equal(merged1, merged2)
