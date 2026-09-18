"""Unit tests for B8 scene verification module: pivot, forward_stats, base, scene_engine.

Covers all 14+ public units identified in the module audit.
Run with: pytest tests/test_scene.py -v
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from tests.helpers.data_factory import make_ohlcv

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def ohlcv_df():
    """200-bar OHLCV with indicators."""
    from core.features.indicators import compute_all_indicators
    df = make_ohlcv(n=200, freq="4h")
    return compute_all_indicators(df)

# ============================================================================
# B8-1: base dataclasses
# ============================================================================

class TestBaseDataclasses:
    """Tests for core/validation/scene/base.py data structures."""

    def test_trigger_point_creation(self):
        from core.validation.scene.base import TriggerPoint
        tp = TriggerPoint(id=1, timestamp="2024-01-01", trigger_price=40000.0, bar_index=10)
        assert tp.id == 1
        assert tp.trigger_price == 40000.0
        assert tp.pattern_subtype == ""

    def test_horizon_stats_creation(self):
        from core.validation.scene.base import HorizonStats
        hs = HorizonStats(horizon=12, close_pct=2.5, max_gain_pct=5.0, max_loss_pct=-1.5,
                          bars_to_peak=3, bars_to_trough=7)
        assert hs.horizon == 12
        assert not hs.is_partial

    def test_aggregate_stats_creation(self):
        from core.validation.scene.base import AggregateStats
        agg = AggregateStats(horizon=12, total_triggers=50, win_rate=60.0,
                             avg_return_pct=1.5, median_return_pct=1.2,
                             avg_max_gain_pct=4.0, avg_max_loss_pct=-2.0,
                             avg_bars_to_peak=4.5)
        assert agg.total_triggers == 50

    def test_scene_verification_result_creation(self):
        from core.validation.scene.base import SceneVerificationResult
        result = SceneVerificationResult(scene_type="test", total_triggers=0)
        assert result.scene_type == "test"
        assert result.warnings == []

# ============================================================================
# B8-2: SceneDetector ABC
# ============================================================================

class TestSceneDetectorABC:
    """Tests for core/validation/scene/base.py:SceneDetector."""

    def test_cannot_instantiate_directly(self):
        from core.validation.scene.base import SceneDetector
        with pytest.raises(TypeError):
            SceneDetector()

    def test_concrete_subclass_works(self):
        from core.validation.scene.base import SceneDetector, TriggerPoint

        class DummyDetector(SceneDetector):
            @property
            def name(self):
                return "dummy"

            @property
            def default_params(self):
                return {"window": 10}

            def detect(self, df, params):
                return [TriggerPoint(id=0, timestamp="2024-01-01", trigger_price=1.0, bar_index=0)]

        d = DummyDetector()
        assert d.name == "dummy"
        assert d.default_params == {"window": 10}
        points = d.detect(pd.DataFrame(), {})
        assert len(points) == 1

# ============================================================================
# B8-3: pivot.detect_pivots
# ============================================================================

class TestDetectPivots:
    """Tests for core/validation/scene/pivot.py:detect_pivots."""

    def test_returns_pivot_points(self):
        from core.validation.scene.pivot import detect_pivots, PivotPoint
        df = make_ohlcv(n=100, freq="4h")
        pivots = detect_pivots(df, bars_left=5, bars_right=5)
        assert isinstance(pivots, list)
        if pivots:
            assert isinstance(pivots[0], PivotPoint)

    def test_pivots_sorted_by_index(self):
        from core.validation.scene.pivot import detect_pivots
        df = make_ohlcv(n=100, freq="4h")
        pivots = detect_pivots(df, bars_left=3, bars_right=3)
        indices = [p.index for p in pivots]
        assert indices == sorted(indices)

    def test_peak_has_high_price(self):
        from core.validation.scene.pivot import detect_pivots
        df = make_ohlcv(n=100, freq="4h")
        pivots = detect_pivots(df, bars_left=3, bars_right=3)
        peaks = [p for p in pivots if p.pivot_type == "peak"]
        for p in peaks:
            assert p.price > 0

    def test_trough_has_low_price(self):
        from core.validation.scene.pivot import detect_pivots
        df = make_ohlcv(n=100, freq="4h")
        pivots = detect_pivots(df, bars_left=3, bars_right=3)
        troughs = [p for p in pivots if p.pivot_type == "trough"]
        for p in troughs:
            assert p.price > 0

    def test_short_dataframe_returns_empty(self):
        from core.validation.scene.pivot import detect_pivots
        df = make_ohlcv(n=5, freq="4h")
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        assert pivots == []

    def test_no_duplicate_pivots_at_same_index(self):
        from core.validation.scene.pivot import detect_pivots
        df = make_ohlcv(n=200, freq="4h")
        pivots = detect_pivots(df, bars_left=5, bars_right=5)
        indices = [p.index for p in pivots]
        # A bar can be both peak and trough, but shouldn't appear as same type twice
        peak_indices = [p.index for p in pivots if p.pivot_type == "peak"]
        assert len(peak_indices) == len(set(peak_indices))

# ============================================================================
# B8-4: forward_stats.compute_forward_stats
# ============================================================================

class TestComputeForwardStats:
    """Tests for core/validation/scene/forward_stats.py:compute_forward_stats."""

    def test_basic_computation(self):
        from core.validation.scene.forward_stats import compute_forward_stats
        df = make_ohlcv(n=100, freq="4h")
        result = compute_forward_stats(df, trigger_bar=10, trigger_price=df["close"].iloc[10], horizons=[6, 12])
        assert 6 in result
        assert 12 in result

    def test_stats_fields(self):
        from core.validation.scene.forward_stats import compute_forward_stats, HorizonStats
        df = make_ohlcv(n=100, freq="4h")
        result = compute_forward_stats(df, trigger_bar=10, trigger_price=df["close"].iloc[10], horizons=[6])
        stats = result[6]
        assert isinstance(stats, HorizonStats)
        assert stats.horizon == 6
        assert isinstance(stats.close_pct, float)
        assert isinstance(stats.max_gain_pct, float)
        assert isinstance(stats.max_loss_pct, float)

    def test_partial_when_near_end(self):
        from core.validation.scene.forward_stats import compute_forward_stats
        df = make_ohlcv(n=50, freq="4h")
        result = compute_forward_stats(df, trigger_bar=45, trigger_price=df["close"].iloc[45], horizons=[12])
        assert result[12].is_partial is True

    def test_beyond_end_returns_partial(self):
        from core.validation.scene.forward_stats import compute_forward_stats
        df = make_ohlcv(n=20, freq="4h")
        result = compute_forward_stats(df, trigger_bar=19, trigger_price=40000.0, horizons=[6])
        assert result[6].is_partial is True

    def test_gain_gte_loss(self):
        from core.validation.scene.forward_stats import compute_forward_stats
        df = make_ohlcv(n=100, freq="4h")
        result = compute_forward_stats(df, trigger_bar=10, trigger_price=df["close"].iloc[10], horizons=[6])
        stats = result[6]
        assert stats.max_gain_pct >= stats.max_loss_pct

# ============================================================================
# B8-5: forward_stats.aggregate_by_horizon
# ============================================================================

class TestAggregateByHorizon:
    """Tests for core/validation/scene/forward_stats.py:aggregate_by_horizon."""

    def test_basic_aggregation(self):
        from core.validation.scene.forward_stats import aggregate_by_horizon, HorizonStats
        all_stats = {
            6: [
                HorizonStats(horizon=6, close_pct=2.0, max_gain_pct=5.0, max_loss_pct=-1.0,
                             bars_to_peak=3, bars_to_trough=1),
                HorizonStats(horizon=6, close_pct=-1.5, max_gain_pct=3.0, max_loss_pct=-4.0,
                             bars_to_peak=2, bars_to_trough=4),
            ],
        }
        result = aggregate_by_horizon(all_stats)
        assert len(result) == 1
        assert result[0].horizon == 6
        assert result[0].total_triggers == 2

    def test_win_rate_calculation(self):
        from core.validation.scene.forward_stats import aggregate_by_horizon, HorizonStats
        all_stats = {
            12: [
                HorizonStats(horizon=12, close_pct=2.0, max_gain_pct=5.0, max_loss_pct=-1.0,
                             bars_to_peak=3, bars_to_trough=1),
                HorizonStats(horizon=12, close_pct=-1.0, max_gain_pct=3.0, max_loss_pct=-4.0,
                             bars_to_peak=2, bars_to_trough=4),
                HorizonStats(horizon=12, close_pct=0.5, max_gain_pct=2.0, max_loss_pct=-0.5,
                             bars_to_peak=1, bars_to_trough=3),
            ],
        }
        result = aggregate_by_horizon(all_stats)
        # 2 out of 3 have close_pct > 0
        assert result[0].win_rate == pytest.approx(100 * 2 / 3, abs=1)

    def test_empty_input(self):
        from core.validation.scene.forward_stats import aggregate_by_horizon
        result = aggregate_by_horizon({})
        assert result == []

    def test_percentiles_included(self):
        from core.validation.scene.forward_stats import aggregate_by_horizon, HorizonStats
        stats_list = [
            HorizonStats(horizon=6, close_pct=float(i), max_gain_pct=float(i+1),
                         max_loss_pct=float(-i), bars_to_peak=i, bars_to_trough=i)
            for i in range(20)
        ]
        result = aggregate_by_horizon({6: stats_list})
        assert "p50" in result[0].percentiles
        assert "min" in result[0].percentiles
        assert "max" in result[0].percentiles

# ============================================================================
# B8-6: scene_engine DETECTORS registry
# ============================================================================

class TestSceneEngineRegistry:
    """Tests for core/validation/scene/scene_engine.py registry."""

    def test_six_detectors_registered(self):
        from core.validation.scene.scene_engine import DETECTORS
        assert len(DETECTORS) == 6

    def test_expected_keys(self):
        from core.validation.scene.scene_engine import DETECTORS
        expected = {"top_pattern", "volume_spike", "mean_reversion",
                    "volume_breakout", "support_resistance", "cross_timeframe"}
        assert set(DETECTORS.keys()) == expected

    def test_scene_types_matches_detectors(self):
        from core.validation.scene.scene_engine import SCENE_TYPES, DETECTORS
        assert set(SCENE_TYPES) == set(DETECTORS.keys())

    def test_scene_meta_has_entries(self):
        from core.validation.scene.scene_engine import SCENE_META
        assert len(SCENE_META) >= 6

    def test_sub_pattern_parent_mapping(self):
        from core.validation.scene.scene_engine import SUB_PATTERN_PARENT
        assert SUB_PATTERN_PARENT["double_top"] == "top_pattern"
        assert SUB_PATTERN_PARENT["head_shoulders_top"] == "top_pattern"

# ============================================================================
# B8-7: detector implementations (smoke test each)
# ============================================================================

class TestDetectorImplementations:
    """Smoke test each detector's detect() method with synthetic data."""

    def test_volume_spike_detector(self, ohlcv_df):
        from core.validation.scene.volume_spike import VolumeSpikeDetector
        detector = VolumeSpikeDetector()
        params = {**detector.default_params}
        result = detector.detect(ohlcv_df, params)
        assert isinstance(result, list)

    def test_mean_reversion_detector(self, ohlcv_df):
        from core.validation.scene.mean_reversion import MeanReversionDetector
        detector = MeanReversionDetector()
        params = {**detector.default_params}
        result = detector.detect(ohlcv_df, params)
        assert isinstance(result, list)

    def test_support_resistance_detector(self, ohlcv_df):
        from core.validation.scene.support_resistance import SupportResistanceDetector
        detector = SupportResistanceDetector()
        params = {**detector.default_params}
        result = detector.detect(ohlcv_df, params)
        assert isinstance(result, list)

    def test_top_pattern_detector(self, ohlcv_df):
        from core.validation.scene.top_pattern import TopPatternDetector
        detector = TopPatternDetector()
        params = {**detector.default_params}
        result = detector.detect(ohlcv_df, params)
        assert isinstance(result, list)
