"""Phase 3 tests: vectorized MTF engine per-bar loops."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.mtf_engine import (
    compute_proximity_score,
    synthesize_cross_layer,
    run_mtf_engine,
)
from tests.helpers.data_factory import make_enhanced_df, make_mtf_dna, make_ohlcv


def _original_compute_proximity_score(price_levels, current_price, s_pct):
    """Original per-bar loop implementation for comparison."""
    if not price_levels or s_pct <= 0:
        return pd.Series(0.0, index=current_price.index)

    n = len(current_price)
    scores = np.zeros(n)

    for bar_idx in range(n):
        price = current_price.iloc[bar_idx]
        if price <= 0:
            continue
        min_rel_dist = float("inf")
        for level_series in price_levels:
            level = level_series.iloc[bar_idx] if bar_idx < len(level_series) else price
            if np.isnan(level) or level <= 0:
                continue
            rel_dist = abs(price - level) / price
            min_rel_dist = min(min_rel_dist, rel_dist)
        if min_rel_dist <= s_pct:
            scores[bar_idx] = max(0.0, 1.0 - min_rel_dist / s_pct)
        else:
            scores[bar_idx] = 0.0

    return pd.Series(scores, index=current_price.index)


class TestProximityScoreVectorizedMatches:
    """Vectorized proximity_score matches original per-bar loop."""

    def test_3_levels_500_bars(self):
        n = 500
        rng = np.random.default_rng(42)
        prices = 40000 + rng.standard_normal(n) * 1000
        current_price = pd.Series(prices, index=pd.RangeIndex(n))

        level1 = pd.Series(prices + rng.standard_normal(n) * 200, index=pd.RangeIndex(n))
        level2 = pd.Series(prices - rng.standard_normal(n) * 200, index=pd.RangeIndex(n))
        level3 = pd.Series(prices + rng.standard_normal(n) * 500, index=pd.RangeIndex(n))

        s_pct = 0.02  # 2%

        new_result = compute_proximity_score([level1, level2, level3], current_price, s_pct)
        old_result = _original_compute_proximity_score([level1, level2, level3], current_price, s_pct)

        np.testing.assert_allclose(new_result.values, old_result.values, rtol=1e-10)

    def test_empty_levels(self):
        current_price = pd.Series([40000.0, 41000.0], index=pd.RangeIndex(2))
        result = compute_proximity_score([], current_price, 0.02)
        assert (result == 0.0).all()

    def test_single_level(self):
        n = 100
        prices = pd.Series(np.linspace(40000, 42000, n), index=pd.RangeIndex(n))
        level = pd.Series(np.linspace(40100, 42100, n), index=pd.RangeIndex(n))
        s_pct = 0.05

        new_result = compute_proximity_score([level], prices, s_pct)
        old_result = _original_compute_proximity_score([level], prices, s_pct)

        np.testing.assert_allclose(new_result.values, old_result.values, rtol=1e-10)

    def test_nan_levels(self):
        n = 50
        prices = pd.Series(40000.0 + np.arange(n) * 10.0, index=pd.RangeIndex(n))
        level = pd.Series(np.where(np.arange(n) % 3 == 0, np.nan, 40000.0 + np.arange(n) * 5.0), index=pd.RangeIndex(n))

        new_result = compute_proximity_score([level], prices, 0.02)
        old_result = _original_compute_proximity_score([level], prices, 0.02)

        np.testing.assert_allclose(new_result.values, old_result.values, rtol=1e-10)


class TestConfluenceMatchesOriginal:
    """Full MTF DNA (3 layers) -> synthesize_cross_layer produces valid output."""

    def test_confluence_output_structure(self):
        """synthesize_cross_layer returns MTFSynthesis with correct structure."""
        from core.strategy.mtf_engine import MTFSynthesis, LayerResult
        from core.strategy.executor import SignalSet

        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = pd.Series(40000.0 + np.arange(n) * 10.0, index=dates)
        atr = pd.Series(500.0, index=dates)

        layer_results = [
            ("1d", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series(False, index=dates),
                    exits=pd.Series(False, index=dates),
                    adds=pd.Series(False, index=dates),
                    reduces=pd.Series(False, index=dates),
                ),
                direction=pd.Series(1.0, index=dates),
                price_levels=[close * 0.99],
            )),
            ("4h", LayerResult(
                signal_set=SignalSet(
                    entries=pd.Series(False, index=dates),
                    exits=pd.Series(False, index=dates),
                    adds=pd.Series(False, index=dates),
                    reduces=pd.Series(False, index=dates),
                ),
            )),
        ]

        dna = make_mtf_dna(timeframes=("1d", "4h"))
        synthesis = synthesize_cross_layer(
            layer_results, dates, close, atr, 1.5, dna,
        )

        assert isinstance(synthesis, MTFSynthesis)
        assert len(synthesis.direction_score) == n
        assert len(synthesis.confluence_score) == n
        assert len(synthesis.momentum_score) == n
        assert len(synthesis.strength_multiplier) == n


class TestMomentumAgreementMatches:
    """Momentum agreement vectorization produces valid output."""

    def test_momentum_fallback_values(self):
        """When no price confluence, momentum agreement should still produce values."""
        from core.strategy.mtf_engine import MTFSynthesis, LayerResult
        from core.strategy.executor import SignalSet

        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = pd.Series(40000.0, index=dates)
        atr = pd.Series(500.0, index=dates)

        # Two layers with momentum, no price levels -> triggers momentum fallback
        mom1 = pd.Series(np.sin(np.arange(n) / 5.0) * 100, index=dates)
        mom2 = pd.Series(np.cos(np.arange(n) / 5.0) * 100, index=dates)

        layer_results = [
            ("1d", LayerResult(
                signal_set=_empty_signal_set_df(dates),
                direction=None,
                momentum=mom1,
            )),
            ("4h", LayerResult(
                signal_set=_empty_signal_set_df(dates),
                direction=None,
                momentum=mom2,
            )),
        ]

        dna = make_mtf_dna(timeframes=("1d", "4h"))
        synthesis = synthesize_cross_layer(
            layer_results, dates, close, atr, 1.5, dna,
        )

        # With 2 momentum layers and no price levels, confluence should come from momentum
        assert isinstance(synthesis.confluence_score, pd.Series)
        assert len(synthesis.confluence_score) == n


def _empty_signal_set_df(dates):
    from core.strategy.executor import SignalSet
    return SignalSet(
        entries=pd.Series(False, index=dates),
        exits=pd.Series(False, index=dates),
        adds=pd.Series(False, index=dates),
        reduces=pd.Series(False, index=dates),
    )


class TestFullMtfEngineRuns:
    """run_mtf_engine() end-to-end produces valid output."""

    def test_run_mtf_engine_basic(self):
        """Basic MTF engine run produces a SignalSet."""
        from core.strategy.executor import SignalSet
        from core.features.indicators import compute_all_indicators

        tf_exec = "4h"
        exec_df = make_ohlcv(n=100, freq="4h")
        exec_df = compute_all_indicators(exec_df)
        # Use same data for structure layer (avoids dimension mismatch in test)
        struct_df = exec_df.copy()

        dfs_by_timeframe = {"4h": exec_df, "1d": struct_df}
        dna = make_mtf_dna(timeframes=("1d", "4h"), mtf_mode="direction+confluence")

        result = run_mtf_engine(dna, dfs_by_timeframe, exec_df)

        assert isinstance(result, SignalSet)
        assert len(result.entries) == len(exec_df)
        assert len(result.exits) == len(exec_df)
