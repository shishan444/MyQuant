"""Tests for new condition types in evaluate_condition."""

import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np

from core.strategy.executor import evaluate_condition

def _make_df(n: int = 50) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame with EMA columns."""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="4h")
    close = 30000 + np.cumsum(np.random.randn(n) * 50)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 10,
        "high": close + abs(np.random.randn(n) * 30),
        "low": close - abs(np.random.randn(n) * 30),
        "close": close,
        "volume": np.random.randint(100, 5000, n).astype(float),
        "ema_10": close - 50 + np.random.randn(n) * 20,
        "ema_20": close - 100 + np.random.randn(n) * 30,
        "rsi_14": np.random.uniform(20, 80, n),
    }, index=dates)
    return df

class TestCrossAboveSeries:
    """Test cross_above_series condition type."""

    def test_detects_cross_above(self):
        df = _make_df(20)
        # Force a cross above event at index 10
        ema_a = df["ema_10"].copy()
        ema_b = df["ema_20"].copy()
        ema_a.iloc[9] = ema_b.iloc[9] - 10  # A below B
        ema_a.iloc[10] = ema_b.iloc[10] + 10  # A above B
        df["ema_10"] = ema_a

        condition = {
            "type": "cross_above_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert result.iloc[10] == True

    def test_no_cross_returns_false(self):
        df = _make_df(20)
        condition = {
            "type": "cross_above_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        # Should be all False for random data (no forced cross)
        assert isinstance(result, pd.Series)

class TestCrossBelowSeries:
    """Test cross_below_series condition type."""

    def test_detects_cross_below(self):
        df = _make_df(20)
        ema_a = df["ema_10"].copy()
        ema_b = df["ema_20"].copy()
        ema_a.iloc[9] = ema_b.iloc[9] + 10  # A above B
        ema_a.iloc[10] = ema_b.iloc[10] - 10  # A below B
        df["ema_10"] = ema_a

        condition = {
            "type": "cross_below_series",
            "target_indicator": "EMA",
            "target_params": {"period": 20},
        }
        result = evaluate_condition(df["ema_10"], df["close"], condition, df=df)
        assert result.iloc[10] == True

class TestLookbackAny:
    """Test lookback_any condition type."""

    def test_any_in_window(self):
        df = _make_df(20)
        rsi = df["rsi_14"].copy()
        # Set RSI > 70 at index 5, check lookback window of 5 at index 8
        rsi.iloc[5] = 75
        rsi.iloc[6] = 40
        rsi.iloc[7] = 40
        rsi.iloc[8] = 40
        df["rsi_14"] = rsi

        condition = {
            "type": "lookback_any",
            "window": 5,
            "inner": {"type": "gt", "threshold": 70},
        }
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        # Index 8 should be True because index 5 had RSI > 70 within window
        assert result.iloc[8] == True

    def test_none_in_window(self):
        df = _make_df(20)
        rsi = df["rsi_14"].copy()
        # All RSI below 60
        rsi.iloc[:] = 40
        df["rsi_14"] = rsi

        condition = {
            "type": "lookback_any",
            "window": 5,
            "inner": {"type": "gt", "threshold": 70},
        }
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        assert result.sum() == 0

class TestLookbackAll:
    """Test lookback_all condition type."""

    def test_all_in_window(self):
        df = _make_df(20)
        rsi = df["rsi_14"].copy()
        # Set RSI > 30 for several consecutive bars
        rsi.iloc[5:10] = 50
        df["rsi_14"] = rsi

        condition = {
            "type": "lookback_all",
            "window": 3,
            "inner": {"type": "gt", "threshold": 30},
        }
        result = evaluate_condition(df["rsi_14"], df["close"], condition, df=df)
        # Should be True at indices where all 3 prior bars are > 30
        assert result.iloc[9] == True

class TestTouchBounce:
    """Test touch_bounce condition type."""

    def test_support_bounce(self):
        df = _make_df(20)
        # Force a support bounce: low near line, close above, current close > previous close
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[9], "close"] = line_val + 50   # previous close lower
        df.loc[df.index[10], "low"] = line_val + 5
        df.loc[df.index[10], "close"] = line_val + 100  # current close higher than prev
        df.loc[df.index[10], "high"] = line_val + 200

        condition = {
            "type": "touch_bounce",
            "direction": "support",
            "proximity_pct": 0.01,
            "bounce_pct": 0.005,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

    def test_resistance_bounce(self):
        df = _make_df(20)
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[9], "close"] = line_val - 50   # previous close higher
        df.loc[df.index[10], "high"] = line_val - 5
        df.loc[df.index[10], "close"] = line_val - 100  # current close lower than prev
        df.loc[df.index[10], "low"] = line_val - 200

        condition = {
            "type": "touch_bounce",
            "direction": "resistance",
            "proximity_pct": 0.01,
            "bounce_pct": 0.005,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

class TestRoleReversal:
    """Test role_reversal condition type."""

    def test_resistance_reversal(self):
        df = _make_df(30)
        line_val = 30000
        df["line"] = line_val
        # 10 bars ago: price above line (line was support)
        df.loc[df.index[10], "close"] = line_val + 200
        # Now: price below line (line became resistance)
        df.loc[df.index[20], "close"] = line_val - 200

        condition = {
            "type": "role_reversal",
            "role": "resistance",
            "lookback": 10,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[20] == True

    def test_support_reversal(self):
        df = _make_df(30)
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[10], "close"] = line_val - 200
        df.loc[df.index[20], "close"] = line_val + 200

        condition = {
            "type": "role_reversal",
            "role": "support",
            "lookback": 10,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[20] == True

class TestWickTouch:
    """Test wick_touch condition type."""

    def test_above_wick_touch(self):
        df = _make_df(20)
        line_val = 30000
        df["line"] = line_val
        # High touches line, but close below
        df.loc[df.index[10], "high"] = line_val + 5
        df.loc[df.index[10], "close"] = line_val - 100
        df.loc[df.index[10], "low"] = line_val - 200

        condition = {
            "type": "wick_touch",
            "direction": "above",
            "proximity_pct": 0.01,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

    def test_below_wick_touch(self):
        df = _make_df(20)
        line_val = 30000
        df["line"] = line_val
        df.loc[df.index[10], "low"] = line_val - 5
        df.loc[df.index[10], "close"] = line_val + 100
        df.loc[df.index[10], "high"] = line_val + 200

        condition = {
            "type": "wick_touch",
            "direction": "below",
            "proximity_pct": 0.01,
        }
        result = evaluate_condition(df["line"], df["close"], condition, df=df)
        assert result.iloc[10] == True

class TestBackwardCompatibility:
    """Ensure original 8 condition types still work with df=None."""

    def test_lt_without_df(self):
        series = pd.Series([1, 2, 3, 4, 5])
        close = pd.Series([3, 3, 3, 3, 3])
        result = evaluate_condition(series, close, {"type": "lt", "threshold": 3})
        assert result.iloc[0] == True
        assert result.iloc[2] == False

    def test_price_above_without_df(self):
        series = pd.Series([2, 4, 2, 4, 2])
        close = pd.Series([3, 3, 3, 3, 3])
        result = evaluate_condition(series, close, {"type": "price_above"})
        assert result.iloc[0] == True
        assert result.iloc[1] == False

    def test_cross_above_without_df(self):
        series = pd.Series([1, 2, 3, 4, 5])
        close = pd.Series([3, 3, 3, 3, 3])
        result = evaluate_condition(series, close, {"type": "cross_above", "threshold": 3})
        assert result.iloc[2] == True
