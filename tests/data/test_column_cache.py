"""Phase 4 tests: indicator column cache for signal computation."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.executor import (
    _get_indicator_column,
    _indicator_column_cache,
    clear_indicator_cache,
    _SimpleGene,
    dna_to_signal_set,
    _empty_signal_set,
)
from tests.helpers.data_factory import make_dna, make_enhanced_df


class TestCacheHitSameObject:
    """Two calls with same df+gene -> return same Series (is operator)."""

    def test_cache_hit(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        gene = _SimpleGene("RSI", {"period": 14})

        result1 = _get_indicator_column(df, gene)
        result2 = _get_indicator_column(df, gene)

        assert result1 is result2
        clear_indicator_cache()


class TestCacheMissDifferentParams:
    """RSI(14) vs RSI(28) -> each resolves correctly."""

    def test_different_params(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)

        gene7 = _SimpleGene("RSI", {"period": 7})
        gene14 = _SimpleGene("RSI", {"period": 14})

        r1 = _get_indicator_column(df, gene7)
        r2 = _get_indicator_column(df, gene14)

        assert r1 is not r2
        # Verify they're actually different columns
        assert r1.name != r2.name
        clear_indicator_cache()


class TestCacheDifferentDfDifferentResult:
    """Different DataFrame -> no cache hit."""

    def test_different_df(self):
        clear_indicator_cache()
        df1 = make_enhanced_df(n=200, seed=42)
        df2 = make_enhanced_df(n=200, seed=99)
        gene = _SimpleGene("RSI", {"period": 14})

        r1 = _get_indicator_column(df1, gene)
        r2 = _get_indicator_column(df2, gene)

        # Different DataFrames have different id() so cache won't hit
        assert r1 is not r2
        clear_indicator_cache()


class TestClearCacheEmpties:
    """clear_indicator_cache() empties the cache."""

    def test_clear(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        gene = _SimpleGene("RSI", {"period": 14})

        _get_indicator_column(df, gene)
        assert len(_indicator_column_cache) > 0

        clear_indicator_cache()
        assert len(_indicator_column_cache) == 0


class TestCacheDoesNotAffectResults:
    """Signal computation results are identical with/without cache."""

    def test_results_consistent(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        dna = make_dna()

        result1 = dna_to_signal_set(dna, df)
        result2 = dna_to_signal_set(dna, df)

        pd.testing.assert_series_equal(result1.entries, result2.entries)
        pd.testing.assert_series_equal(result1.exits, result2.exits)
        clear_indicator_cache()


class TestCacheAcrossIndividuals:
    """15 individuals sharing RSI(14) -> only parsed once per df."""

    def test_shared_indicator_cached(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)

        individuals = [make_dna() for _ in range(15)]
        for ind in individuals:
            dna_to_signal_set(ind, df)

        # All individuals use RSI(14) entry + RSI(14) exit
        # With caching, there should be very few unique cache entries
        # (one for entry gene, one for exit gene, since they share same params)
        assert len(_indicator_column_cache) <= 3
        clear_indicator_cache()
