"""Tests for on-demand indicator computation in _get_indicator_column.

Validates that:
- Precomputed columns are returned without triggering on-demand compute
- Missing columns trigger _compute_indicator and the result is written to df
- Computed columns are reused for subsequent lookups within the same df
- Indicators not in registry still raise ValueError
"""
import pytest

import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from core.features.indicators import _compute_indicator, _DEFAULT_PARAMS
from core.features.registry import INDICATOR_REGISTRY
from tests.helpers.data_factory import make_ohlcv, make_signal_gene

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gene_df_with_rsi_14():
    """Return (gene, df) where df has rsi_14 precomputed."""
    df = make_ohlcv(n=200)
    df["rsi_14"] = _compute_indicator(df, "RSI", {"period": 14})["rsi_14"]
    gene = make_signal_gene(indicator="RSI", params={"period": 14},
                            condition_type="lt", condition_value=30)
    return gene, df


def _gene_df_without_rsi_99():
    """Return (gene, df) where rsi_99 is NOT in df."""
    df = make_ohlcv(n=200)
    gene = make_signal_gene(indicator="RSI", params={"period": 99},
                            condition_type="lt", condition_value=30)
    return gene, df


# ---------------------------------------------------------------------------
# S1.1: Precomputed columns hit first (zero overhead)
# ---------------------------------------------------------------------------

class TestPrecomputedPriority:
    """Precomputed columns must be returned without on-demand compute."""

    def test_precomputed_column_returned_directly(self):
        gene, df = _gene_df_with_rsi_14()
        from core.strategy.executor import _get_indicator_column
        result = _get_indicator_column(df, gene)
        assert result is not None
        assert len(result) == 200
        assert "rsi_14" in df.columns

    def test_on_demand_not_called_for_precomputed(self):
        gene, df = _gene_df_with_rsi_14()
        with patch("core.features.indicators._compute_indicator") as mock:
            from core.strategy.executor import _get_indicator_column
            _get_indicator_column(df, gene)
            mock.assert_not_called()


# ---------------------------------------------------------------------------
# S1.2: On-demand compute fallback
# ---------------------------------------------------------------------------

class TestOnDemandCompute:
    """Missing columns should trigger _compute_indicator and return result."""

    def test_missing_column_triggers_compute(self):
        gene, df = _gene_df_without_rsi_99()
        assert "rsi_99" not in df.columns

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        # Column should now exist in df
        assert "rsi_99" in df.columns
        assert result is not None
        assert len(result) == 200

    def test_compute_result_written_to_df(self):
        gene, df = _gene_df_without_rsi_99()
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        _get_indicator_column(df, gene)

        # The computed column must be in df for reuse
        assert "rsi_99" in df.columns

    def test_unknown_indicator_still_raises(self):
        df = make_ohlcv(n=200)
        gene = make_signal_gene(indicator="FakeIndicator", params={},
                                condition_type="lt", condition_value=1)
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        with pytest.raises(ValueError):
            _get_indicator_column(df, gene)

    def test_compute_returns_empty_still_raises(self):
        """If _compute_indicator returns empty dict, ValueError still raised."""
        gene, df = _gene_df_without_rsi_99()
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()

        with patch("core.features.indicators._compute_indicator", return_value={}):
            with pytest.raises(ValueError):
                _get_indicator_column(df, gene)


# ---------------------------------------------------------------------------
# S1.3: Computed column reuse within same generation
# ---------------------------------------------------------------------------

class TestComputeReuse:
    """On-demand computed columns must be reusable without recomputation."""

    def test_second_lookup_uses_cached_column(self):
        gene, df = _gene_df_without_rsi_99()
        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()

        # First lookup triggers compute
        result1 = _get_indicator_column(df, gene)
        assert "rsi_99" in df.columns

        # Second lookup should NOT recompute (use cache)
        with patch("core.features.indicators._compute_indicator") as mock:
            result2 = _get_indicator_column(df, gene)
            mock.assert_not_called()

        pd.testing.assert_series_equal(result1, result2)

    def test_different_genes_same_params_reuse_column(self):
        """Two genes with same (indicator, params) should share the df column."""
        gene1, df = _gene_df_without_rsi_99()
        gene2 = make_signal_gene(indicator="RSI", params={"period": 99},
                                  role=gene1.role, condition_type="gt",
                                  condition_value=70)

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()

        result1 = _get_indicator_column(df, gene1)
        assert "rsi_99" in df.columns

        with patch("core.features.indicators._compute_indicator") as mock:
            result2 = _get_indicator_column(df, gene2)
            mock.assert_not_called()

        assert len(result1) == len(result2) == 200


# ---------------------------------------------------------------------------
# S1.4: Multi-output indicator on-demand (e.g. MACD, BB)
# ---------------------------------------------------------------------------

class TestMultiOutputOnDemand:
    """Multi-output indicators (MACD, BB) should work with on-demand compute."""

    def test_macd_non_default_params(self):
        df = make_ohlcv(n=300)
        gene = make_signal_gene(indicator="MACD",
                                params={"fast": 8, "slow": 21, "signal": 5},
                                condition_type="cross_above",
                                condition_value=0,
                                field_name="histogram")
        assert "macd_8_21_5_histogram" not in df.columns

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        assert result is not None
        assert len(result) == 300

    def test_bb_non_default_std(self):
        df = make_ohlcv(n=200)
        gene = make_signal_gene(indicator="BB",
                                params={"period": 20, "std": 1.5},
                                condition_type="price_below",
                                field_name="lower")
        assert "bb_20_1.5_lower" not in df.columns

        from core.strategy.executor import _get_indicator_column, clear_indicator_cache
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        assert result is not None
        assert len(result) == 200
