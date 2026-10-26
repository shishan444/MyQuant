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


# ---------------------------------------------------------------------------
# S1.5: Substring fallback retirement (fix-executor-column-and-exit-guard A1/A2)
# ---------------------------------------------------------------------------

class TestSubstringFallbackRetirement:
    """精确列名 miss 必须落按需现算，不得子串命中他参数列。

    修复前缺陷：请求 rsi_2 时 "rsi_2" in "rsi_21" 子串命中，静默返回
    RSI(21) 列，现算路径永不可达（BUG-1 主证）。
    """

    def test_rsi_2_computed_exact_not_substring_rsi_21(self):
        df = make_ohlcv(n=200)
        for p in (7, 14, 21):
            df[f"rsi_{p}"] = _compute_indicator(df, "RSI", {"period": p})[f"rsi_{p}"]
        gene = make_signal_gene(indicator="RSI", params={"period": 2},
                                condition_type="lt", condition_value=10)

        from core.strategy.executor import (
            _get_indicator_column, clear_indicator_cache,
        )
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        expected = _compute_indicator(df, "RSI", {"period": 2})["rsi_2"]
        # check_names=False：pandas-ta 产 Series.name 为库内命名（RSI_2）
        # 与 dict 键/df 列名（rsi_2）不同源——锚的本意是逐元素值一致
        pd.testing.assert_series_equal(
            result.reset_index(drop=True),
            expected.reset_index(drop=True), check_names=False)
        assert result.name == "rsi_2"
        assert "rsi_2" in df.columns

    def test_ema_5_computed_exact_not_substring_ema_50(self):
        df = make_ohlcv(n=200)
        for p in (10, 20, 50, 100, 200):
            df[f"ema_{p}"] = _compute_indicator(df, "EMA", {"period": p})[f"ema_{p}"]
        gene = make_signal_gene(indicator="EMA", params={"period": 5},
                                condition_type="gt", condition_value=0)

        from core.strategy.executor import (
            _get_indicator_column, clear_indicator_cache,
        )
        clear_indicator_cache()
        result = _get_indicator_column(df, gene)

        expected = _compute_indicator(df, "EMA", {"period": 5})["ema_5"]
        pd.testing.assert_series_equal(
            result.reset_index(drop=True),
            expected.reset_index(drop=True), check_names=False)
        assert result.name == "ema_5"
        assert "ema_5" in df.columns

    def test_a9_naming_desync_fails_loud_with_available_columns(self):
        """A9：naming 失同步形态——resolve 产物与现算产列名仅子串
        关系（"macd_8_21_5_histogram" vs 实际 "macd_8_21_5" /
        "macd_histogram_8_21_5"）→ 显式 ValueError 附实际可用列清单
        （互含取列退役：不静默拿错语义分量，fail-loud 暴露失同步）。
        """
        df = make_ohlcv(n=300)
        gene = make_signal_gene(indicator="MACD",
                                params={"fast": 8, "slow": 21, "signal": 5},
                                condition_type="cross_above",
                                condition_value=0,
                                field_name="histogram")
        from core.strategy.executor import (
            _get_indicator_column, clear_indicator_cache,
        )
        clear_indicator_cache()
        # 故障注入：resolve 层失同步产出词序畸形列名（仅与现算产列
        # 成子串关系——旧互含分支会静默取 macd_8_21_5 错语义分量）
        with patch("core.strategy.executor.resolve_indicator_column",
                   return_value="macd_8_21_5_histogram"):
            with pytest.raises(ValueError) as exc_info:
                _get_indicator_column(df, gene)
        msg = str(exc_info.value)
        assert "Available" in msg
        assert "macd_8_21_5" in msg        # 实际可用列清单在案
