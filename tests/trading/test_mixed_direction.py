"""Phase C: mixed direction bidirectional trading (BUG-5).

Verifies that direction="mixed" strategies can produce both long and short
trades based on trend direction signals.
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set
from core.backtest.engine import BacktestEngine

def _make_ohlcv(n=300, timeframe="4h", seed=42):
    """Create synthetic OHLCV DataFrame with indicators."""
    np.random.seed(seed)
    freq_map = {"1h": "1h", "4h": "4h", "1d": "1D"}
    freq = freq_map.get(timeframe, "4h")
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    df["rsi_14"] = 50.0
    df["ema_50"] = close.mean()
    return df

def _make_trending_data(n=300):
    """Create data that trends up then down for mixed testing.

    First half: prices rise (uptrend)
    Second half: prices fall (downtrend)
    """
    np.random.seed(55)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    # Rising first half, falling second half
    half = n // 2
    up = np.linspace(38000, 42000, half)
    down = np.linspace(42000, 38000, n - half)
    close = np.concatenate([up, down])
    close += np.random.randn(n) * 20

    df = pd.DataFrame({
        "open": close * 0.9999, "high": close * 1.002,
        "low": close * 0.998, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    # RSI triggers for entries/exits in both halves
    rsi = np.full(n, 50.0)
    rsi[30] = 25.0   # uptrend entry
    rsi[60] = 25.0   # uptrend entry
    rsi[170] = 25.0  # downtrend entry
    rsi[200] = 25.0  # downtrend entry
    rsi[45] = 75.0
    rsi[80] = 75.0
    rsi[185] = 75.0
    rsi[220] = 75.0
    df["rsi_14"] = rsi

    # EMA: direction indicator
    # Uptrend: EMA below price -> price_above=True -> long direction
    # Downtrend: EMA above price -> price_above=False -> short direction
    ema = np.full(n, 40000.0)
    ema[:half] = close[:half] * 0.998
    ema[half:] = close[half:] * 1.002
    df["ema_50"] = ema

    return df

def _make_mixed_mtf_dna(direction="mixed", cross_layer_logic="OR"):
    """Create MTF DNA with mixed direction and trend-based direction.

    Uses OR logic so exec triggers can fire regardless of trend direction.
    Trend layer provides direction info (+1 or -1).
    """
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10,
            position_size=0.5, leverage=1,
            direction=direction,
        ),
        layers=[
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 30}),
                    SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                                {"type": "gt", "threshold": 70}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                role="execution",
            ),
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
                role="trend",
            ),
        ],
        cross_layer_logic=cross_layer_logic,
    )

def _make_single_tf_mixed_dna(direction="mixed"):
    """Create single-TF mixed DNA for simpler tests."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10,
            position_size=0.5, leverage=1,
            direction=direction,
        ),
    )

def test_mixed_signal_set_has_entry_direction():
    """MTF mixed strategy should produce entry_direction in SignalSet."""
    dna = _make_mixed_mtf_dna("mixed")
    df = _make_trending_data()
    dfs = {"4h": df}

    sig = dna_to_signal_set(dna, df, dfs_by_timeframe=dfs)

    assert hasattr(sig, "entry_direction"), "SignalSet should have entry_direction field"
    assert sig.entry_direction is not None, "MTF mixed should have non-None entry_direction"

def test_mixed_entry_direction_values():
    """entry_direction should be +1 for uptrend and -1 for downtrend."""
    dna = _make_mixed_mtf_dna("mixed")
    df = _make_trending_data()
    dfs = {"4h": df}

    sig = dna_to_signal_set(dna, df, dfs_by_timeframe=dfs)

    half = len(df) // 2
    # Uptrend bars should have positive direction
    uptrend_dirs = sig.entry_direction.iloc[:half]
    # Downtrend bars should have negative direction
    downtrend_dirs = sig.entry_direction.iloc[half:]

    assert (uptrend_dirs > 0).any(), "Some uptrend bars should have positive direction"
    assert (downtrend_dirs < 0).any(), "Some downtrend bars should have negative direction"

def test_mixed_produces_both_long_and_short_trades():
    """Mixed strategy should produce both Long and Short trades."""
    dna = _make_mixed_mtf_dna("mixed")
    df = _make_trending_data()
    dfs = {"4h": df}

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df, dfs_by_timeframe=dfs)

    assert result.total_trades > 0, "Should have at least some trades"

    trades_df = result.trades_df
    assert trades_df is not None and len(trades_df) > 0

    # Check for both directions using "Direction" column
    directions = trades_df["Direction"].unique()
    assert len(directions) >= 2, (
        f"Expected both Long and Short trades, got directions: {directions}"
    )

def test_mixed_differs_from_long():
    """Mixed strategy should produce different results from long-only."""
    df = _make_trending_data()
    dfs = {"4h": df}
    engine = BacktestEngine(init_cash=100000)

    result_mixed = engine.run(_make_mixed_mtf_dna("mixed"), df, dfs_by_timeframe=dfs)
    result_long = engine.run(_make_mixed_mtf_dna("long"), df, dfs_by_timeframe=dfs)

    different = (
        result_mixed.total_trades != result_long.total_trades
        or abs(result_mixed.total_return - result_long.total_return) > 0.001
    )
    assert different, (
        f"Mixed should differ from long. "
        f"Mixed: {result_mixed.total_trades} trades, return={result_mixed.total_return:.4f}. "
        f"Long: {result_long.total_trades} trades, return={result_long.total_return:.4f}"
    )

def test_mixed_differs_from_short():
    """Mixed strategy should produce different results from short-only."""
    df = _make_trending_data()
    dfs = {"4h": df}
    engine = BacktestEngine(init_cash=100000)

    result_mixed = engine.run(_make_mixed_mtf_dna("mixed"), df, dfs_by_timeframe=dfs)
    result_short = engine.run(_make_mixed_mtf_dna("short"), df, dfs_by_timeframe=dfs)

    different = (
        result_mixed.total_trades != result_short.total_trades
        or abs(result_mixed.total_return - result_short.total_return) > 0.001
    )
    assert different, (
        f"Mixed should differ from short. "
        f"Mixed: {result_mixed.total_trades} trades, return={result_mixed.total_return:.4f}. "
        f"Short: {result_short.total_trades} trades, return={result_short.total_return:.4f}"
    )

def test_mixed_sl_tp_works_both_directions():
    """SL/TP should work for both long and short positions in mixed mode."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10,
            position_size=0.5, leverage=1,
            direction="mixed",
        ),
    )
    df = _make_ohlcv(300)
    df["rsi_14"] = 50.0
    df.iloc[10, df.columns.get_loc("rsi_14")] = 25.0
    df.iloc[20, df.columns.get_loc("rsi_14")] = 25.0

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert isinstance(result.total_return, float)

def test_long_still_works():
    """direction='long' should still work normally."""
    dna = _make_single_tf_mixed_dna("long")
    df = _make_ohlcv(200)
    df["rsi_14"] = 50.0
    df.iloc[10, df.columns.get_loc("rsi_14")] = 25.0
    df.iloc[15, df.columns.get_loc("rsi_14")] = 75.0

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert isinstance(result.total_return, float)

def test_short_still_works():
    """direction='short' should still work normally."""
    dna = _make_single_tf_mixed_dna("short")
    df = _make_ohlcv(200)
    df["rsi_14"] = 50.0
    df.iloc[10, df.columns.get_loc("rsi_14")] = 25.0
    df.iloc[15, df.columns.get_loc("rsi_14")] = 75.0

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert isinstance(result.total_return, float)

def test_single_tf_mixed_has_direction_signal():
    """Single-TF mixed strategy: entry_direction should be generated from DIRECTION gene."""
    dna = _make_single_tf_mixed_dna("mixed")
    # Add a DIRECTION gene (required for mixed strategies)
    dna = StrategyDNA(
        signal_genes=dna.signal_genes + [
            SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION, None,
                        {"type": "price_above"}),
        ],
        logic_genes=dna.logic_genes,
        execution_genes=dna.execution_genes,
        risk_genes=dna.risk_genes,
    )
    df = _make_trending_data()

    sig = dna_to_signal_set(dna, df)
    assert sig.entry_direction is not None, (
        "Single-TF mixed strategy should generate entry_direction from DIRECTION gene"
    )
    # Should have both +1 and -1 values (up and down trend)
    assert (sig.entry_direction > 0).any(), "Should have positive direction (uptrend)"
    assert (sig.entry_direction < 0).any(), "Should have negative direction (downtrend)"


def test_single_tf_mixed_differs_from_long():
    """Single-TF mixed strategy should differ from long-only on trending data."""
    df = _make_trending_data()
    engine = BacktestEngine(init_cash=100000)

    result_mixed = engine.run(_make_single_tf_mixed_dna("mixed"), df)
    result_long = engine.run(_make_single_tf_mixed_dna("long"), df)

    different = abs(result_mixed.total_return - result_long.total_return) > 0.001
    assert different, (
        f"Single-TF mixed should differ from long. "
        f"Mixed return={result_mixed.total_return:.4f}, "
        f"Long return={result_long.total_return:.4f}"
    )
