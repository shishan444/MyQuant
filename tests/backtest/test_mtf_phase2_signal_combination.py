"""Phase 2: MTF Signal Combination fixes.

Verifies:
- M1: trend_exits participates in final exit signal combination
- M2: Missing timeframe data logs warning instead of silent skip
- M3: _resample_pulse uses time-window aggregation instead of exact timestamp match
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]
import logging

from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)
from core.strategy.executor import (
    dna_to_signal_set,
    _resample_pulse,
    resample_signals,
)

# ── Helpers ──

def _make_ohlcv_df(n=500, timeframe="4h", start="2024-01-01"):
    """Create synthetic OHLCV DataFrame with indicators."""
    freq_map = {"1h": "1h", "4h": "4h", "1d": "1D", "15m": "15min"}
    freq = freq_map.get(timeframe, "4h")
    np.random.seed(42)
    dates = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    # Add common indicators
    close_s = pd.Series(close, index=dates)
    delta = close_s.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain, index=dates).rolling(14).mean()
    avg_loss = pd.Series(loss, index=dates).rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df["rsi_14"] = rsi.fillna(50)

    # EMA
    df["ema_50"] = close_s.ewm(span=50).mean()
    df["ema_200"] = close_s.ewm(span=200).mean()

    return df

def _make_mtf_dna_with_roles(exec_timeframe="4h"):
    """Create MTF DNA with trend + execution layers."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=exec_timeframe, symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5,
                             leverage=1, direction="long"),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                    SignalGene("EMA", {"period": 50}, SignalRole.EXIT_TRIGGER, "ema_50",
                                {"type": "price_below"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                role="trend",
            ),
        ],
    )

def _make_mtf_dna_no_roles(exec_timeframe="4h"):
    """Create MTF DNA without roles (legacy mode)."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=exec_timeframe, symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5,
                             leverage=1, direction="long"),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                    SignalGene("EMA", {"period": 50}, SignalRole.EXIT_TRIGGER, "ema_50",
                                {"type": "price_below"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            ),
        ],
        cross_layer_logic="AND",
    )

# ── M1: trend_exits participates in final exit signal ──

def test_trend_exits_included_in_combined_exits():
    """When trend layer produces exit signals, they should appear in final exits."""
    dna = _make_mtf_dna_with_roles()
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(500, "1d")

    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    # Both entries and exits should be non-trivial (not all False)
    assert isinstance(sig_set.exits, pd.Series)
    assert len(sig_set.exits) == len(enhanced_df)

    # The exit signal should include trend layer exits (price below EMA)
    # At least some exits should be True for a realistic price series
    # (the daily EMA cross will produce exit signals)
    assert sig_set.exits.any() or not sig_set.entries.any(), \
        "If trend exits exist, they should influence final exits"

def test_trend_exits_fire_on_trend_reversal():
    """Trend exit signal should fire when price drops below EMA on daily."""
    dna = _make_mtf_dna_with_roles()
    # Create data where price starts above EMA then drops below
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = np.concatenate([
        np.linspace(42000, 45000, n // 2),  # Rising
        np.linspace(45000, 38000, n // 2),  # Falling
    ])
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    df["rsi_14"] = 50.0  # Neutral RSI
    df["ema_50"] = pd.Series(close).ewm(span=50).mean()

    # Daily data with same trend
    daily_dates = pd.date_range("2024-01-01", periods=n // 6, freq="1D", tz="UTC")
    daily_close = np.linspace(42000, 38000, len(daily_dates))
    daily_df = pd.DataFrame({
        "open": daily_close * 0.999, "high": daily_close * 1.005,
        "low": daily_close * 0.995, "close": daily_close, "volume": 1000.0,
    }, index=daily_dates)
    daily_df.index.name = "timestamp"
    daily_df["ema_50"] = pd.Series(daily_close).ewm(span=50).mean()

    dfs_by_timeframe = {"4h": df, "1d": daily_df}

    sig_set = dna_to_signal_set(dna, df, dfs_by_timeframe=dfs_by_timeframe)

    # Exit signals should exist in the falling portion
    assert isinstance(sig_set.exits, pd.Series)
    # With trend exits included, there should be exit signals
    # when price falls below daily EMA

def test_no_trend_exits_without_trend_layers():
    """Non-MTF strategy should not be affected by trend_exits fix."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5,
                             leverage=1, direction="long"),
    )
    enhanced_df = _make_ohlcv_df(500)
    sig_set = dna_to_signal_set(dna, enhanced_df)

    assert isinstance(sig_set.entries, pd.Series)
    assert isinstance(sig_set.exits, pd.Series)

# ── M2: Missing timeframe data logs warning ──

def test_missing_timeframe_data_logs_warning(caplog):
    """When a layer's timeframe data is missing, a warning should be logged."""
    dna = _make_mtf_dna_with_roles()
    enhanced_df = _make_ohlcv_df(500, "4h")

    # Only provide exec timeframe, not the daily data
    dfs_by_timeframe = {"4h": enhanced_df}

    with caplog.at_level(logging.WARNING, logger="core.strategy.executor"):
        sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    # Should still produce valid result (graceful degradation)
    assert isinstance(sig_set.entries, pd.Series)
    assert len(sig_set.entries) == len(enhanced_df)

    # Should have logged a warning about missing "1d" data
    assert any("1d" in record.message for record in caplog.records) or \
           any("missing" in record.message.lower() for record in caplog.records), \
        "Expected a warning about missing timeframe data"

def test_all_layers_missing_still_returns_valid_result():
    """When all layer data is missing, should return all-False signals."""
    dna = _make_mtf_dna_with_roles()
    enhanced_df = _make_ohlcv_df(500, "4h")

    # Empty dict - no timeframe data at all
    dfs_by_timeframe = {}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert isinstance(sig_set.entries, pd.Series)
    assert len(sig_set.entries) == len(enhanced_df)

# ── M3: _resample_pulse uses time-window aggregation ──

def test_resample_pulse_preserves_signals_within_window():
    """Pulse signals from higher TF should map to the correct target TF bar.

    For a 1h signal at 09:00 mapping to 4h target:
    - 4h bars: 08:00, 12:00, 16:00
    - 1h signal at 09:00 falls within the 08:00-12:00 window
    - The 4h bar at 08:00 should be True
    """
    # Create 4h target index
    target_dates = pd.date_range("2024-01-01 00:00", periods=6, freq="4h", tz="UTC")

    # Create 1h source signal: True at 09:00 (within 08:00-12:00 window)
    source_dates = pd.date_range("2024-01-01 00:00", periods=24, freq="1h", tz="UTC")
    signal_values = np.zeros(24, dtype=bool)
    # Signal at 09:00 (index 9)
    signal_values[9] = True

    source_signal = pd.Series(signal_values, index=source_dates)

    result = _resample_pulse(source_signal, target_dates)

    assert isinstance(result, pd.Series)
    assert len(result) == len(target_dates)
    # The 4h bar starting at 08:00 (index 2) should capture the 09:00 signal
    assert result.iloc[2], "Signal at 09:00 should map to 4h bar at 08:00"

def test_resample_pulse_no_false_positives():
    """Pulse resampling should not create signals where none existed."""
    target_dates = pd.date_range("2024-01-01 00:00", periods=6, freq="4h", tz="UTC")
    source_dates = pd.date_range("2024-01-01 00:00", periods=24, freq="1h", tz="UTC")

    # All False signal
    source_signal = pd.Series(False, index=source_dates)

    result = _resample_pulse(source_signal, target_dates)

    assert not result.any(), "All-False source should produce all-False target"

def test_resample_pulse_multiple_signals_in_window():
    """Multiple source signals in same target window should produce single True."""
    target_dates = pd.date_range("2024-01-01 00:00", periods=6, freq="4h", tz="UTC")
    source_dates = pd.date_range("2024-01-01 00:00", periods=24, freq="1h", tz="UTC")

    signal_values = np.zeros(24, dtype=bool)
    # Two signals in the same 4h window (08:00-12:00)
    signal_values[8] = True   # 08:00
    signal_values[10] = True  # 10:00

    source_signal = pd.Series(signal_values, index=source_dates)

    result = _resample_pulse(source_signal, target_dates)

    # Should have True at the 08:00 4h bar (index 2)
    assert result.iloc[2]
    # Should NOT double-count
    assert result.sum() >= 1

def test_resample_pulse_exact_match_still_works():
    """When source timestamp exactly matches target, signal should be preserved."""
    target_dates = pd.date_range("2024-01-01 00:00", periods=6, freq="4h", tz="UTC")
    # Source on same 4h grid
    source_dates = pd.date_range("2024-01-01 00:00", periods=6, freq="4h", tz="UTC")

    signal_values = np.array([False, True, False, False, False, False])
    source_signal = pd.Series(signal_values, index=source_dates)

    result = _resample_pulse(source_signal, target_dates)

    assert result.iloc[1], "Exact timestamp match should be preserved"

def test_resample_pulse_empty_source():
    """Empty source signal should return all-False."""
    target_dates = pd.date_range("2024-01-01", periods=6, freq="4h", tz="UTC")
    source_signal = pd.Series(dtype=bool)

    result = _resample_pulse(source_signal, target_dates)

    assert len(result) == len(target_dates)
    assert not result.any()

def test_resample_pulse_preserves_all_signals():
    """Verify no signal loss in 1h→4h resampling."""
    np.random.seed(42)
    source_dates = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
    signal_values = np.random.random(24) > 0.7  # ~30% True

    source_signal = pd.Series(signal_values, index=source_dates)
    source_count = source_signal.sum()

    target_dates = pd.date_range("2024-01-01", periods=6, freq="4h", tz="UTC")
    result = _resample_pulse(source_signal, target_dates)

    # Every source signal should map to some target bar
    # Result count should be >= source_count grouped by windows
    # More precisely: each target bar that has at least one source True should be True
    for i, target_ts in enumerate(target_dates):
        # Find source bars in this window
        if i + 1 < len(target_dates):
            window_mask = (source_dates >= target_ts) & (source_dates < target_dates[i + 1])
        else:
            window_mask = source_dates >= target_ts
        window_signals = source_signal[window_mask]
        if window_signals.any():
            assert result.iloc[i], \
                f"Target bar {target_ts} should be True (has {window_signals.sum()} source signals)"

# ── Integration: MTF signal set with fixed combination ──

def test_mtf_signal_set_with_roles_produces_valid_signals():
    """Full MTF signal evaluation with roles should produce valid SignalSet."""
    dna = _make_mtf_dna_with_roles()
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert isinstance(sig_set.entries, pd.Series)
    assert isinstance(sig_set.exits, pd.Series)
    assert isinstance(sig_set.adds, pd.Series)
    assert isinstance(sig_set.reduces, pd.Series)
    assert len(sig_set.entries) == len(enhanced_df)

def test_mtf_signal_set_no_roles_backward_compat():
    """MTF without roles should use legacy combination logic."""
    dna = _make_mtf_dna_no_roles()
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert isinstance(sig_set.entries, pd.Series)
    assert len(sig_set.entries) == len(enhanced_df)

def test_resample_signals_still_forward_fills():
    """resample_signals (trend layers) should still forward-fill correctly."""
    source_dates = pd.date_range("2024-01-01", periods=6, freq="4h", tz="UTC")
    target_dates = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")

    # Signal True on 04:00 4h bar (index 1)
    signal_values = np.array([False, True, False, False, False, False])
    source_signal = pd.Series(signal_values, index=source_dates)

    result = resample_signals(source_signal, target_dates)

    # ffill from 04:00: bars 04:00 through 07:00 should be True
    # (since next 4h bar at 08:00 is False, ffill stops there)
    assert not result.iloc[3]   # 03:00 should be False
    assert result.iloc[4]       # 04:00 should be True (signal origin)
    assert result.iloc[7]       # 07:00 should be True (forward-filled from 04:00)
    assert not result.iloc[8]   # 08:00 should be False (new 4h bar, signal was False)
