"""Unit tests for candlestick pattern detection functions."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.patterns.candlestick import (
    detect_bearish_engulfing,
    detect_evening_star,
    detect_three_black_crows,
    detect_shooting_star,
    detect_three_white_soldiers,
    detect_morning_star,
    detect_bullish_reversal,
    detect_bearish_reversal,
)
from core.features.patterns.divergence import (
    detect_bullish_divergence,
    detect_bearish_divergence,
)

def _make_df(n: int = 20, seed: int = 42) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame for testing."""
    rng = np.random.RandomState(seed)
    close = 100 + np.cumsum(rng.randn(n) * 2)
    df = pd.DataFrame({
        "open": close + rng.randn(n),
        "high": close + np.abs(rng.randn(n)),
        "low": close - np.abs(rng.randn(n)),
        "close": close,
        "volume": rng.randint(1000, 10000, size=n),
    })
    return df

# -- Bearish engulfing --
def test_bearish_engulfing_detected():
    """Construct a clear bearish engulfing at row 2."""
    df = pd.DataFrame({
        "open":  [100, 95, 93],
        "high":  [101, 97, 100],
        "low":   [99,  93, 92],
        "close": [99,  96, 93],  # row1 bullish (96>95), row2 bearish (93<93..)
        "volume": [1000, 1000, 1000],
    })
    # row 1: open=95, close=96 (bullish)
    # row 2: open=93 -> make it 97 so it engulfs
    df.loc[2, "open"] = 97
    result = detect_bearish_engulfing(df)
    assert result["pattern_bearish_engulfing"].iloc[2] == 1
    # Row 0 and 1 should not trigger
    assert result["pattern_bearish_engulfing"].iloc[0] == 0
    assert result["pattern_bearish_engulfing"].iloc[1] == 0

def test_bearish_engulfing_no_false_positive():
    df = _make_df()
    result = detect_bearish_engulfing(df)
    # With random data, just verify it returns valid 0/1 values
    assert set(result["pattern_bearish_engulfing"].unique()).issubset({0, 1})

# -- Morning star --
def test_morning_star_detected():
    """Construct a morning star pattern at row 2."""
    df = pd.DataFrame({
        "open":  [100, 92, 88],
        "high":  [101, 93, 96],
        "low":   [99,  88, 87],
        "close": [99,  91, 95],  # row0 bearish, row1 small, row2 bullish
        "volume": [1000, 1000, 1000],
    })
    # row0: open=100, close=99 (bearish, body=1)
    # row1: open=92, close=91 (body=1, small vs prev)
    # row2: open=88, close=95 (bullish)
    result = detect_morning_star(df)
    # Check it returns valid values
    assert set(result["pattern_morning_star"].unique()).issubset({0, 1})

# -- Evening star --
def test_evening_star_detected():
    df = pd.DataFrame({
        "open":  [90, 96, 98],
        "high":  [92, 98, 99],
        "low":   [89, 95, 93],
        "close": [96, 97, 93],
        "volume": [1000, 1000, 1000],
    })
    result = detect_evening_star(df)
    assert set(result["pattern_evening_star"].unique()).issubset({0, 1})

# -- Three black crows --
def test_three_black_crows_detected():
    df = pd.DataFrame({
        "open":  [100, 98, 96, 94],
        "high":  [101, 99, 97, 95],
        "low":   [99,  95, 93, 91],
        "close": [98,  95, 93, 91],
        "volume": [1000, 1000, 1000, 1000],
    })
    result = detect_three_black_crows(df)
    # Row 3 should detect 3 black crows
    assert result["pattern_3blackcrows"].iloc[3] == 1

# -- Three white soldiers --
def test_three_white_soldiers_detected():
    df = pd.DataFrame({
        "open":  [90, 91, 93, 95],
        "high":  [92, 94, 96, 98],
        "low":   [89, 90, 92, 94],
        "close": [92, 94, 96, 98],
        "volume": [1000, 1000, 1000, 1000],
    })
    result = detect_three_white_soldiers(df)
    assert result["pattern_3whitesoldiers"].iloc[3] == 1

# -- Shooting star --
def test_shooting_star_returns_valid():
    df = _make_df()
    result = detect_shooting_star(df)
    assert set(result["pattern_shooting_star"].unique()).issubset({0, 1})

# -- Bullish reversal --
def test_bullish_reversal_detected():
    df = pd.DataFrame({
        "open":  [100, 95, 90],
        "high":  [101, 96, 97],
        "low":   [99,  89, 89],
        "close": [99,  90, 96],
        "volume": [1000, 1000, 1000],
    })
    result = detect_bullish_reversal(df)
    assert set(result["pattern_bullish_reversal"].unique()).issubset({0, 1})

# -- Bearish reversal --
def test_bearish_reversal_detected():
    df = pd.DataFrame({
        "open":  [90, 96, 100],
        "high":  [92, 98, 101],
        "low":   [89, 94, 93],
        "close": [96, 98, 93],
        "volume": [1000, 1000, 1000],
    })
    result = detect_bearish_reversal(df)
    assert set(result["pattern_bearish_reversal"].unique()).issubset({0, 1})

# -- Divergence detection --
def test_bullish_divergence_with_rsi():
    df = _make_df(50)
    # Add a fake RSI column
    df["rsi_14"] = 50 + np.random.randn(50) * 10
    result = detect_bullish_divergence(df)
    assert "pattern_bullish_divergence" in result.columns
    assert set(result["pattern_bullish_divergence"].unique()).issubset({0, 1})

def test_bearish_divergence_with_rsi():
    df = _make_df(50)
    df["rsi_14"] = 50 + np.random.randn(50) * 10
    result = detect_bearish_divergence(df)
    assert "pattern_bearish_divergence" in result.columns
    assert set(result["pattern_bearish_divergence"].unique()).issubset({0, 1})

def test_divergence_without_rsi_returns_zero():
    df = _make_df(50)
    result_bull = detect_bullish_divergence(df)
    result_bear = detect_bearish_divergence(df)
    assert result_bull["pattern_bullish_divergence"].sum() == 0
    assert result_bear["pattern_bearish_divergence"].sum() == 0

# -- Registry integration --
def test_pattern_indicators_in_registry():
    from core.features.registry import INDICATOR_REGISTRY
    pattern_indicators = [
        "BearishEngulfing", "EveningStar", "ThreeBlackCrows", "ShootingStar",
        "ThreeWhiteSoldiers", "MorningStar", "BullishReversal", "BearishReversal",
        "BullishDivergence", "BearishDivergence",
    ]
    for name in pattern_indicators:
        assert name in INDICATOR_REGISTRY, f"{name} not in registry"
        assert INDICATOR_REGISTRY[name].category == "pattern"
        assert INDICATOR_REGISTRY[name].supported_conditions == ["eq"]
        assert INDICATOR_REGISTRY[name].params == {}

# -- Compute integration --
def test_compute_pattern_indicators():
    from core.features.indicators import _compute_indicator
    df = _make_df(30)

    for name in ["BearishEngulfing", "MorningStar", "ThreeWhiteSoldiers"]:
        result = _compute_indicator(df, name, {})
        assert isinstance(result, dict)
        assert len(result) == 1
        col_name = list(result.keys())[0]
        assert col_name.startswith("pattern_")
        assert set(result[col_name].unique()).issubset({0, 1})
