"""Factor computation unit tests.

Covers:
1. All 8 factors compute without error on enhanced df
2. Factor values are in expected ranges
3. Handles edge case: insufficient history (returns defaults)
4. Factor keys match FACTOR_POOL definition
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = [pytest.mark.unit]


@pytest.fixture
def enhanced_df():
    """Create a small enhanced DataFrame with indicators."""
    from tests.helpers.data_factory import make_ohlcv
    from core.features.indicators import compute_all_indicators
    df = make_ohlcv(n=300, seed=42)
    return compute_all_indicators(df)


def _make_dna():
    from core.prediction.genes import PredictionDNA
    return PredictionDNA(short_window=15, mid_window=60, long_window=200)


class TestComputeFactors:
    def test_returns_all_factors(self, enhanced_df):
        from core.prediction.factors import compute_factors, FACTOR_POOL
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        assert set(factors.keys()) == set(FACTOR_POOL.keys())

    def test_vol_regime_positive(self, enhanced_df):
        from core.prediction.factors import compute_factors
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        assert factors["vol_regime"] > 0

    def test_bb_squeeze_range(self, enhanced_df):
        from core.prediction.factors import compute_factors
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        # bb_squeeze = 1 - percentile_rank, should be in [0, 1]
        assert 0.0 <= factors["bb_squeeze"] <= 1.0

    def test_rvol_positive(self, enhanced_df):
        from core.prediction.factors import compute_factors
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        assert factors["rvol"] > 0

    def test_tension_non_negative(self, enhanced_df):
        from core.prediction.factors import compute_factors
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        assert factors["tension_short"] >= 0
        assert factors["tension_mid"] >= 0
        assert factors["tension_long"] >= 0

    def test_adx_strength_range(self, enhanced_df):
        from core.prediction.factors import compute_factors
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        assert 0.0 <= factors["adx_strength"] <= 1.0

    def test_tension_divergence_can_be_negative(self, enhanced_df):
        from core.prediction.factors import compute_factors
        # Just verify it's a number (can be positive or negative)
        idx = len(enhanced_df) - 1
        factors = compute_factors(enhanced_df, idx, _make_dna())
        assert isinstance(factors["tension_divergence"], float)


class TestComputeFactorsEdgeCases:
    def test_short_history_no_crash(self):
        from core.prediction.factors import compute_factors
        from tests.helpers.data_factory import make_ohlcv
        from core.features.indicators import compute_all_indicators
        df = make_ohlcv(n=30, seed=99)
        df = compute_all_indicators(df)
        # Should not crash even with limited history
        factors = compute_factors(df, len(df) - 1, _make_dna())
        assert len(factors) > 0
