"""Extended unit tests for scene detectors and pattern_match.

Covers: VolumeBreakoutDetector, CrossTimeframeDetector,
        pattern_match.match_patterns, scene_engine.run_scene_verification.

Run with: pytest tests/test_scene_ext.py -v
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers: synthetic data construction
# ---------------------------------------------------------------------------

def _make_ohlcv_with_bb(n: int = 60, base_price: float = 40000.0) -> pd.DataFrame:
    """Build a small OHLCV DataFrame with a Bollinger Band upper column.

    Volume is flat except for one deliberate spike; the BB upper band is set
    slightly below the close at the spike bar so the breakout condition fires.
    """
    rng = np.random.default_rng(123)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    # Gradual uptrend
    closes = base_price + np.arange(n, dtype=float) * 5.0
    highs = closes + rng.uniform(10, 50, n)
    lows = closes - rng.uniform(10, 50, n)
    opens = closes - rng.uniform(-20, 20, n)

    # Flat volume, then a huge spike at bar 40
    volume = np.full(n, 500.0)
    volume[40] = 5000.0  # 10x normal

    # BB upper sits just below close on bar 40 so breakout triggers
    bb_upper = closes + 10.0  # slightly above most closes
    bb_upper[40] = closes[40] - 5.0  # close exceeds band at spike bar

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "bb_upper_20_2.0": bb_upper,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


def _make_flat_ohlcv_with_bb(n: int = 60, base_price: float = 40000.0) -> pd.DataFrame:
    """Flat prices and flat volume -- no breakout expected."""
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    closes = np.full(n, base_price)
    highs = closes + 1.0
    lows = closes - 1.0
    opens = closes.copy()
    volume = np.full(n, 500.0)

    bb_upper = np.full(n, base_price + 100.0)  # far above close

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "bb_upper_20_2.0": bb_upper,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


# ===========================================================================
# 1. VolumeBreakoutDetector
# ===========================================================================

class TestVolumeBreakoutDetector:
    """Tests for core/validation/scene/volume_breakout.py:VolumeBreakoutDetector."""

    def test_volume_breakout_detector_detect(self):
        """Spike in volume + close above BB upper should produce TriggerPoint(s)."""
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector
        from core.validation.scene.base import TriggerPoint

        df = _make_ohlcv_with_bb()
        det = VolumeBreakoutDetector()
        results = det.detect(df, det.default_params)

        assert isinstance(results, list)
        # At least one trigger at the volume-spike bar (bar 40)
        assert len(results) >= 1
        assert isinstance(results[0], TriggerPoint)
        assert results[0].trigger_price > 0
        # The spike bar should be among the triggers
        spike_bars = [tp.bar_index for tp in results]
        assert 40 in spike_bars

    def test_volume_breakout_detector_no_signal(self):
        """Flat volume and flat price should yield zero triggers."""
        from core.validation.scene.volume_breakout import VolumeBreakoutDetector

        df = _make_flat_ohlcv_with_bb()
        det = VolumeBreakoutDetector()
        results = det.detect(df, det.default_params)

        assert results == []


# ===========================================================================
# 2. CrossTimeframeDetector
# ===========================================================================

class TestCrossTimeframeDetector:
    """Tests for core/validation/scene/cross_timeframe.py:CrossTimeframeDetector."""

    def test_cross_timeframe_detector_requires_mtf_columns(self):
        """Without higher-TF EMA columns, detect() should return empty list."""
        from core.validation.scene.cross_timeframe import CrossTimeframeDetector

        # Build a plain OHLCV frame with no MTF columns
        dates = pd.date_range("2024-01-01", periods=60, freq="4h", tz="UTC")
        rng = np.random.default_rng(99)
        n = 60
        df = pd.DataFrame(
            {
                "open": rng.uniform(39000, 41000, n),
                "high": rng.uniform(40000, 42000, n),
                "low": rng.uniform(38000, 40000, n),
                "close": rng.uniform(39000, 41000, n),
                "volume": rng.uniform(100, 5000, n),
            },
            index=dates,
        )
        df.index.name = "timestamp"

        det = CrossTimeframeDetector()
        results = det.detect(df, det.default_params)

        # No MTF columns => graceful empty result
        assert results == []


# ===========================================================================
# 3. PatternMatch & match_patterns
# ===========================================================================

class TestPatternMatch:
    """Tests for core/validation/scene/pattern_match.py:match_patterns."""

    @staticmethod
    def _build_double_top_df() -> pd.DataFrame:
        """Craft ~100 bars of OHLCV that form a clean double-top pattern.

        Layout (bar indices, using default pivot window bars_left=6, bars_right=6):
          bar 15: peak_1  (high = 110)
          bar 35: trough  (low = 90)
          bar 55: peak_2  (high = 110, same height)
          bar 55-70: decline confirming the pattern

        Key: every bar within 6 bars of a peak has a strictly lower high,
        and every bar within 6 bars of the trough has a strictly higher low.
        """
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

        # Start with baseline 100 everywhere
        closes = np.full(n, 100.0)
        highs = np.full(n, 100.5)
        lows = np.full(n, 99.5)
        volume = np.full(n, 500.0)

        # --- Peak 1 region (bar 15): climb then descend ---
        for i in range(9, 15):
            closes[i] = 100.0 + (i - 9) * 1.5
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5
        closes[15] = 110.0
        highs[15] = 110.0
        lows[15] = 108.5
        volume[15] = 1000.0
        for i in range(16, 22):
            closes[i] = 110.0 - (i - 15) * 1.5
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5

        # --- Trough region (bar 35): descend then ascend ---
        for i in range(29, 35):
            closes[i] = 101.0 - (i - 29) * 1.5
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5
        closes[35] = 90.0
        highs[35] = 91.0
        lows[35] = 90.0
        volume[35] = 400.0
        for i in range(36, 42):
            closes[i] = 90.0 + (i - 35) * 1.5
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5

        # --- Peak 2 region (bar 55): climb then descend ---
        for i in range(49, 55):
            closes[i] = 100.0 + (i - 49) * 1.5
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5
        closes[55] = 110.0
        highs[55] = 110.0
        lows[55] = 108.5
        volume[55] = 900.0  # slightly less than peak 1 (volume constraint)
        for i in range(56, 62):
            closes[i] = 110.0 - (i - 55) * 1.5
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5

        # After peak 2: continue declining (confirmation)
        for i in range(62, 75):
            closes[i] = max(85.0, closes[61] - (i - 61) * 0.5)
            highs[i] = closes[i] + 0.5
            lows[i] = closes[i] - 0.5

        df = pd.DataFrame(
            {"open": closes, "high": highs, "low": lows, "close": closes, "volume": volume},
            index=dates,
        )
        df.index.name = "timestamp"
        return df

    def test_pattern_match_double_top(self):
        """Construct data forming a double-top and verify match_patterns detects it."""
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots

        df = self._build_double_top_df()
        # Use default TopPatternDetector pivot params
        pivots = detect_pivots(df, bars_left=6, bars_right=6)
        results = match_patterns(pivots, df=df)

        # Should find at least one double-top pattern
        double_tops = [m for m in results if m.pattern_type == "double_top"]
        assert len(double_tops) >= 1, (
            f"Expected at least one double_top, got {[m.pattern_type for m in results]}. "
            f"Pivots found: {[(p.pivot_type, p.index, p.price) for p in pivots]}"
        )
        dt = double_tops[0]
        assert dt.completion_bar > 0
        assert len(dt.key_points) >= 3  # peak_1, trough, peak_2

    def test_pattern_match_insufficient_data(self):
        """Fewer than 3 pivots should return empty list."""
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import PivotPoint

        # Only 2 pivots -- below the minimum of 3
        pivots = [
            PivotPoint(index=0, price=100.0, pivot_type="peak"),
            PivotPoint(index=5, price=90.0, pivot_type="trough"),
        ]
        results = match_patterns(pivots, df=pd.DataFrame({"high": [100], "low": [90]}))
        assert results == []

    def test_pattern_match_flat_data(self):
        """Completely flat price data should yield no patterns."""
        from core.validation.scene.pattern_match import match_patterns
        from core.validation.scene.pivot import detect_pivots

        n = 60
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        flat = np.full(n, 100.0)
        df = pd.DataFrame(
            {"open": flat, "high": flat, "low": flat, "close": flat, "volume": np.full(n, 500.0)},
            index=dates,
        )
        df.index.name = "timestamp"

        pivots = detect_pivots(df, bars_left=5, bars_right=5)
        # Flat data => zero pivots => match_patterns returns []
        results = match_patterns(pivots, df=df)
        assert results == []


# ===========================================================================
# 4. run_scene_verification
# ===========================================================================

class TestRunSceneVerification:
    """Tests for core/validation/scene/scene_engine.py:run_scene_verification."""

    def _make_synthetic_df(self, n: int = 200) -> pd.DataFrame:
        """Quick OHLCV frame with volume spike for mock injection."""
        from tests.helpers.data_factory import make_ohlcv
        return make_ohlcv(n=n, freq="4h")

    def test_run_scene_verification_basic(self):
        """Mock data loading and indicator computation; verify result structure."""
        from core.validation.scene.scene_engine import run_scene_verification

        raw_df = self._make_synthetic_df()

        # Build an enhanced version that has a BB upper column so
        # VolumeBreakoutDetector can actually find signals.
        enhanced_df = raw_df.copy()
        enhanced_df["bb_upper_20_2.0"] = enhanced_df["close"] + 50.0
        # Inject a volume spike + breakout at bar 50
        enhanced_df.iloc[50, enhanced_df.columns.get_loc("volume")] = 100000.0
        enhanced_df.iloc[50, enhanced_df.columns.get_loc("bb_upper_20_2.0")] = (
            enhanced_df.iloc[50]["close"] - 10.0
        )
        # Also inject the minimum indicator columns _extract_snapshot looks for
        enhanced_df["atr_14"] = 100.0

        with patch("core.validation.scene.scene_engine.load_parquet", return_value=raw_df), \
             patch("core.validation.scene.scene_engine.compute_all_indicators", return_value=enhanced_df):

            result = run_scene_verification(
                symbol="BTCUSDT",
                timeframe="4h",
                scene_type="volume_breakout",
                data_dir="/tmp/fake_data",
            )

        from core.validation.scene.base import SceneVerificationResult
        assert isinstance(result, SceneVerificationResult)
        assert result.scene_type == "volume_breakout"

    def test_run_scene_verification_unknown_detector(self):
        """An unrecognised scene_type should return empty result with warning."""
        from core.validation.scene.scene_engine import run_scene_verification
        from core.validation.scene.base import SceneVerificationResult

        result = run_scene_verification(
            symbol="BTCUSDT",
            timeframe="4h",
            scene_type="nonexistent_pattern",
            data_dir="/tmp/fake_data",
        )

        assert isinstance(result, SceneVerificationResult)
        assert result.total_triggers == 0
        # Should contain a warning about unknown scene type
        assert any("Unknown" in w for w in result.warnings)
