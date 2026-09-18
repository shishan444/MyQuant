"""Tests for pattern detection algorithms."""

import pytest

pytestmark = [pytest.mark.unit]
import numpy as np
import pandas as pd

from core.validation.patterns import (
    detect_divergence_top,
    detect_divergence_bottom,
    detect_consecutive_up,
    detect_consecutive_down,
    detect_touch_bounce,
    detect_role_reversal,
)

def _make_df(price: list[float], indicator: list[float]) -> pd.DataFrame:
    """Build a simple DataFrame with 'close' and 'rsi' columns."""
    return pd.DataFrame({
        "close": price,
        "rsi": indicator,
    })

# ---------------------------------------------------------------------------
# detect_divergence_top (bearish divergence)
# ---------------------------------------------------------------------------

class TestDetectDivergenceTop:
    def test_no_divergence_flat(self):
        """Flat price and flat indicator should produce no signals."""
        n = 50
        df = _make_df([100.0] * n, [50.0] * n)
        result = detect_divergence_top(df, "rsi")
        assert result.sum() == 0

    def test_bearish_divergence_detected(self):
        """Price higher high + RSI lower high should trigger."""
        n = 60
        price = np.linspace(100, 100, n).astype(float)
        rsi = np.linspace(50, 50, n).astype(float)

        # First peak: price=105, rsi=70
        price[15] = 105
        rsi[15] = 70

        # Dip between peaks
        price[22] = 100
        rsi[22] = 55

        # Second peak: price higher (108 > 105), RSI lower (65 < 70)
        price[30] = 108
        rsi[30] = 65

        df = _make_df(price.tolist(), rsi.tolist())
        result = detect_divergence_top(df, "rsi", lookback=20)
        # Should detect divergence at some bar near or after the second peak
        assert result.sum() >= 1

    def test_missing_column_returns_all_false(self):
        """Missing subject column should return all False."""
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = detect_divergence_top(df, "nonexistent")
        assert len(result) == 3
        assert result.sum() == 0

# ---------------------------------------------------------------------------
# detect_divergence_bottom (bullish divergence)
# ---------------------------------------------------------------------------

class TestDetectDivergenceBottom:
    def test_no_divergence_flat(self):
        n = 50
        df = _make_df([100.0] * n, [50.0] * n)
        result = detect_divergence_bottom(df, "rsi")
        assert result.sum() == 0

    def test_bullish_divergence_detected(self):
        """Price lower low + RSI higher low should trigger."""
        n = 60
        price = np.linspace(100, 100, n).astype(float)
        rsi = np.linspace(50, 50, n).astype(float)

        # First trough: price=95, rsi=30
        price[15] = 95
        rsi[15] = 30

        # Bounce between troughs
        price[22] = 100
        rsi[22] = 50

        # Second trough: price lower (92 < 95), RSI higher (35 > 30)
        price[30] = 92
        rsi[30] = 35

        df = _make_df(price.tolist(), rsi.tolist())
        result = detect_divergence_bottom(df, "rsi", lookback=20)
        assert result.sum() >= 1

    def test_missing_column_returns_all_false(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = detect_divergence_bottom(df, "nonexistent")
        assert len(result) == 3
        assert result.sum() == 0

# ---------------------------------------------------------------------------
# detect_consecutive_up
# ---------------------------------------------------------------------------

class TestDetectConsecutiveUp:
    def test_strictly_increasing_sequence(self):
        """5 consecutive increasing values with count=3."""
        df = _make_df(
            [100, 101, 102, 103, 104, 105],
            [50.0, 50.0, 50.0, 50.0, 50.0, 50.0],
        )
        # Use close as subject
        result = detect_consecutive_up(df, "close", count=3)
        # Bars at index 3,4,5 should be True (3+ consecutive rising)
        assert result.iloc[3] is True or result.iloc[3] == True
        assert result.iloc[4] is True or result.iloc[4] == True
        assert result.iloc[5] is True or result.iloc[5] == True

    def test_not_increasing_at_start(self):
        """First 'count' bars should be False (not enough history)."""
        df = _make_df([100, 101, 102, 103], [50.0] * 4)
        result = detect_consecutive_up(df, "close", count=3)
        assert result.iloc[0] == False
        assert result.iloc[1] == False

    def test_flat_values_not_increasing(self):
        """Equal values should not count as increasing."""
        df = _make_df([100, 100, 100, 100], [50.0] * 4)
        result = detect_consecutive_up(df, "close", count=3)
        assert result.sum() == 0

    def test_missing_column(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = detect_consecutive_up(df, "nonexistent", count=2)
        assert result.sum() == 0

# ---------------------------------------------------------------------------
# detect_consecutive_down
# ---------------------------------------------------------------------------

class TestDetectConsecutiveDown:
    def test_strictly_decreasing_sequence(self):
        df = _make_df(
            [105, 104, 103, 102, 101, 100],
            [50.0] * 6,
        )
        result = detect_consecutive_down(df, "close", count=3)
        assert result.iloc[3] is True or result.iloc[3] == True
        assert result.iloc[4] is True or result.iloc[4] == True
        assert result.iloc[5] is True or result.iloc[5] == True

    def test_increasing_not_decreasing(self):
        df = _make_df([100, 101, 102, 103, 104], [50.0] * 5)
        result = detect_consecutive_down(df, "close", count=3)
        assert result.sum() == 0

    def test_missing_column(self):
        df = pd.DataFrame({"close": [3, 2, 1]})
        result = detect_consecutive_down(df, "nonexistent", count=2)
        assert result.sum() == 0

# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_nan_values_in_consecutive(self):
        """NaN in subject column should not trigger consecutive."""
        df = _make_df([100, 101, np.nan, 103, 104], [50.0] * 5)
        result = detect_consecutive_up(df, "close", count=3)
        assert result.sum() == 0

    def test_short_dataframe_divergence(self):
        """DataFrame shorter than lookback should produce no signals."""
        df = _make_df([100, 101, 102], [50, 55, 60])
        result = detect_divergence_top(df, "rsi", lookback=20)
        assert result.sum() == 0

    def test_single_bar_dataframe(self):
        """Single bar should produce no patterns."""
        df = _make_df([100], [50])
        assert detect_consecutive_up(df, "close", count=1).sum() == 0
        assert detect_consecutive_down(df, "close", count=1).sum() == 0

# ---------------------------------------------------------------------------
# detect_touch_bounce
# ---------------------------------------------------------------------------

class TestDetectTouchBounce:
    def _make_ohlcv(self, n: int = 30) -> pd.DataFrame:
        """Build OHLCV DataFrame with a line column."""
        np.random.seed(42)
        close = np.full(n, 100.0)
        return pd.DataFrame({
            "open": close - 1,
            "high": close + 5,
            "low": close - 5,
            "close": close,
            "volume": np.random.randint(100, 1000, n).astype(float),
            "line": np.full(n, 100.0),
        })

    def test_support_bounce_detected(self):
        df = self._make_ohlcv(30)
        # Bar 10: low touches line, close above, bar 11 closes higher
        df.loc[df.index[10], "low"] = 100.5
        df.loc[df.index[10], "close"] = 101.0
        df.loc[df.index[10], "high"] = 102.0
        df.loc[df.index[11], "close"] = 102.0  # Bounce above 101 * 1.005 = 101.505

        result = detect_touch_bounce(df, "line", direction="support", proximity_pct=0.01)
        assert result.iloc[10] == True

    def test_resistance_bounce_detected(self):
        df = self._make_ohlcv(30)
        df.loc[df.index[10], "high"] = 99.5
        df.loc[df.index[10], "close"] = 99.0
        df.loc[df.index[10], "low"] = 98.0
        df.loc[df.index[11], "close"] = 98.5

        result = detect_touch_bounce(df, "line", direction="resistance", proximity_pct=0.01)
        assert result.iloc[10] == True

    def test_missing_column_returns_false(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = detect_touch_bounce(df, "nonexistent", direction="support")
        assert result.sum() == 0

# ---------------------------------------------------------------------------
# detect_role_reversal
# ---------------------------------------------------------------------------

class TestDetectRoleReversal:
    def test_resistance_reversal(self):
        n = 30
        df = pd.DataFrame({
            "close": np.full(n, 100.0),
            "line": np.full(n, 100.0),
        })
        # 10 bars ago: price above line (line was support)
        df.loc[df.index[5], "close"] = 105.0
        # Now: price below line (line became resistance)
        df.loc[df.index[15], "close"] = 95.0

        result = detect_role_reversal(df, "line", role="resistance", lookback=10)
        assert result.iloc[15] == True

    def test_support_reversal(self):
        n = 30
        df = pd.DataFrame({
            "close": np.full(n, 100.0),
            "line": np.full(n, 100.0),
        })
        df.loc[df.index[5], "close"] = 95.0
        df.loc[df.index[15], "close"] = 105.0

        result = detect_role_reversal(df, "line", role="support", lookback=10)
        assert result.iloc[15] == True

    def test_missing_column_returns_false(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = detect_role_reversal(df, "nonexistent", role="resistance")
        assert result.sum() == 0
