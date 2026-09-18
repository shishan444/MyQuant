"""Tests for CrossTimeframeDetector: higher-TF EMA cross confirmed by base-TF entry.

Covers:
- Golden cross detection (fast crosses above slow on higher TF)
- Alternate column naming fallback
- Entry confirmation via base-TF fast MA
- Min spacing between triggers
- Empty/missing column handling
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.validation.scene.cross_timeframe import CrossTimeframeDetector
from core.validation.scene.base import TriggerPoint


@pytest.fixture
def detector():
    return CrossTimeframeDetector()


def _make_cross_df(n=100, cross_bar=50):
    """Build a DataFrame with higher-TF EMA columns that produce a golden cross.

    The cross happens at cross_bar where fast goes from <= slow to > slow.
    Base-TF ema_20 is set low so close > ema_20 (entry confirmation passes).
    """
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    closes = np.full(n, 60000.0)

    df = pd.DataFrame(
        {"open": closes * 0.999, "high": closes * 1.005,
         "low": closes * 0.995, "close": closes,
         "volume": np.full(n, 100.0)},
        index=dates,
    )
    df.index.name = "timestamp"

    # Higher-TF EMA columns with golden cross at cross_bar
    fast = np.full(n, 59900.0)   # fast < slow before cross
    slow = np.full(n, 60100.0)
    # At cross_bar: fast > slow (golden cross)
    fast[cross_bar:] = 60200.0
    slow[cross_bar:] = 60100.0   # slow stays same

    df["ema_20_1d"] = fast
    df["ema_50_1d"] = slow

    # Base-TF fast EMA below close so entry confirmation passes
    df["ema_20"] = 58000.0

    return df


class TestCrossTimeframeDetection:
    """Core detection logic: golden cross on higher TF."""

    def test_detects_golden_cross(self, detector):
        """Should detect at least 1 trigger when golden cross occurs."""
        df = _make_cross_df(cross_bar=50)
        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        assert len(triggers) >= 1
        assert all(isinstance(t, TriggerPoint) for t in triggers)

    def test_trigger_at_cross_bar(self, detector):
        """Trigger should fire at the golden cross bar (bar 50)."""
        df = _make_cross_df(cross_bar=50)
        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        bar_indices = [t.bar_index for t in triggers]
        assert 50 in bar_indices

    def test_trigger_has_htf_snapshot(self, detector):
        """Trigger should include htf_fast and htf_slow in snapshot."""
        df = _make_cross_df(cross_bar=50)
        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        assert len(triggers) >= 1
        snap = triggers[0].indicator_snapshot
        assert "htf_fast" in snap
        assert "htf_slow" in snap
        assert snap["htf_fast"] > snap["htf_slow"]  # golden cross means fast > slow

    def test_no_cross_no_trigger(self, detector):
        """When fast stays below slow, no triggers should fire."""
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        closes = np.full(n, 60000.0)
        df = pd.DataFrame(
            {"open": closes * 0.999, "high": closes * 1.005,
             "low": closes * 0.995, "close": closes, "volume": [100] * n},
            index=dates,
        )
        df.index.name = "timestamp"
        # fast always below slow
        df["ema_20_1d"] = 59900.0
        df["ema_50_1d"] = 60100.0

        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        assert len(triggers) == 0


class TestAlternateNaming:
    """Detector should fall back to alternate column naming."""

    def test_fallback_to_ema_prefix(self, detector):
        """Should find columns even when signal_type doesn't match prefix."""
        df = _make_cross_df(cross_bar=50)
        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "sma_cross",
                                        "fast_period": 20, "slow_period": 50})
        # Columns are ema_20_1d/ema_50_1d which match the fallback
        assert len(triggers) >= 1


class TestMissingColumns:
    """Detector should handle missing columns gracefully."""

    def test_no_higher_tf_columns_returns_empty(self, detector):
        """Should return empty list when higher-TF columns are missing."""
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"open": [100] * n, "high": [101] * n, "low": [99] * n,
             "close": [100] * n, "volume": [50] * n},
            index=dates,
        )
        df.index.name = "timestamp"

        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        assert triggers == []


class TestEntryConfirmation:
    """Base-TF entry confirmation should filter triggers."""

    def test_entry_blocked_when_close_below_base_ma(self, detector):
        """Trigger should be filtered when close < base-TF fast MA."""
        df = _make_cross_df(cross_bar=50)
        # Set base-TF ema above close to block entry
        df["ema_20"] = df["close"] * 1.5

        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        assert len(triggers) == 0

    def test_entry_allowed_when_no_base_ma(self, detector):
        """Without base-TF EMA column, cross still triggers."""
        df = _make_cross_df(cross_bar=50)
        df.drop(columns=["ema_20"], inplace=True)

        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50})
        assert len(triggers) >= 1


class TestMinSpacing:
    """min_spacing should prevent triggers too close together."""

    def test_min_spacing_enforced(self, detector):
        """Multiple crosses close together should only produce spaced triggers."""
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        closes = np.full(n, 60000.0)
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes,
             "close": closes, "volume": [100] * n},
            index=dates,
        )
        df.index.name = "timestamp"

        # Create 4 crosses, only first and last are >= min_spacing=10 apart
        fast = np.full(n, 59900.0)
        slow = np.full(n, 60100.0)
        # Cross at bars 20, 22, 24, 35
        for cross_bar in [20, 22, 24]:
            fast[cross_bar] = 60200.0
        # Last cross at bar 35, which is 15 bars after bar 20
        fast[35] = 60200.0

        df["ema_20_1d"] = fast
        df["ema_50_1d"] = slow

        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50,
                                        "min_spacing": 10})
        # With min_spacing=10: bar 20 fires, bar 22 is too close (diff=2),
        # bar 24 is too close (diff=4 from 20), bar 35 is 15 from 20 -> fires
        assert len(triggers) == 2
        assert triggers[0].bar_index == 20
        assert triggers[1].bar_index == 35

    def test_min_spacing_zero(self, detector):
        """With min_spacing=0, all crosses should trigger."""
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        closes = np.full(n, 60000.0)
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes,
             "close": closes, "volume": [100] * n},
            index=dates,
        )
        df.index.name = "timestamp"

        fast = np.full(n, 59900.0)
        slow = np.full(n, 60100.0)
        # Cross at bars 30, 40, 50, 60
        for cross_bar in [30, 40, 50, 60]:
            fast[cross_bar] = 60200.0

        df["ema_20_1d"] = fast
        df["ema_50_1d"] = slow

        triggers = detector.detect(df, {"higher_tf": "1d", "signal_type": "ema_cross",
                                        "fast_period": 20, "slow_period": 50,
                                        "min_spacing": 0})
        assert len(triggers) == 4
