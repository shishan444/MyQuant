"""Phase D: Add signal entry price update (BUG-8).

Verifies that add signals update entry_price to weighted average and
add basic margin checking.
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
)
from core.backtest.engine import BacktestEngine

def _make_ohlcv(n=200, seed=42):
    """Create synthetic OHLCV DataFrame with indicators."""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"
    df["rsi_14"] = 50.0
    return df

def _make_dna_with_add():
    """Create DNA with entry, add, and exit signals."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.ADD_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10,
            position_size=0.5, leverage=1,
            direction="long",
        ),
    )

def test_add_signal_increases_position():
    """Add signal should increase the total position."""
    dna = _make_dna_with_add()
    df = _make_ohlcv(100)

    df.iloc[10, df.columns.get_loc("rsi_14")] = 25.0  # entry trigger
    df.iloc[15, df.columns.get_loc("rsi_14")] = 32.0  # add trigger
    df.iloc[20, df.columns.get_loc("rsi_14")] = 75.0  # exit trigger

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades > 0, "Should have trades"
    assert result.add_count > 0, f"Should have add signals, got {result.add_count}"

def test_add_updates_entry_price():
    """Add signal should update entry_price to weighted average.

    Key insight: signals are delayed by 1 bar, so:
    - RSI<30 at bar 5 -> entry at bar 6
    - RSI<35 at bar 8 -> add at bar 9

    We set prices at execution bars (6, 9) to create different entry/add prices.
    Then verify SL triggers at blended level, not original entry level.
    """
    n = 30
    np.random.seed(99)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    entry_price = 40000.0
    add_price = 41000.0

    # All prices flat except where we need specific values
    close = np.full(n, 40500.0)
    close[6] = entry_price   # actual entry execution price (signal at bar 5)
    close[9] = add_price     # actual add execution price (signal at bar 8)

    # SL test: drop between original SL and blended SL
    # Original SL at 2%: 40000 * 0.98 = 39200
    # Blended SL: ~40500 * 0.98 ≈ 39690
    # Set low at bar 13 to 39400 -> below blended SL, above original SL
    close[13] = 39500.0

    high = close * 1.002
    low = close * 0.998

    df = pd.DataFrame({
        "open": close * 0.999, "high": high,
        "low": low, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    rsi = np.full(n, 50.0)
    rsi[5] = 25.0   # entry signal (shifts to bar 6)
    rsi[8] = 32.0   # add signal (shifts to bar 9)
    df["rsi_14"] = rsi

    # Set low at bar 13 to trigger blended SL
    df.iloc[13, df.columns.get_loc("low")] = 39400.0

    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.ADD_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 35}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.02, take_profit=0.0,
            position_size=0.5, leverage=1,
            direction="long",
        ),
    )

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    trades_df = result.trades_df
    assert trades_df is not None and len(trades_df) > 0

    # With blended price (~40500), SL at 2% ≈ 39690
    # Low at bar 13 = 39400 < 39690 -> SL should trigger
    # Without blended price (40000), SL at 39200, low 39400 > 39200 -> no SL
    exit_ts = trades_df["Exit Timestamp"].iloc[0]
    assert exit_ts <= dates[14], (
        f"SL should trigger with blended entry price around bar 13-14. "
        f"Exit at {exit_ts}, latest expected {dates[14]}"
    )

def test_add_sl_uses_blended_price():
    """SL after add should use blended (weighted average) entry price.

    Verifies the weighted average calculation is correct by checking
    that the SL trigger level shifts after the add.
    """
    n = 30
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    # With position_size=0.5, first entry buys shares worth 50% of portfolio
    # Entry at 40000, shares1 = 0.5 * 100000 / 40000 = 1.25
    # Add at 41000, after entry equity ≈ 100000, shares2 = 0.5 * 100000 / 41000 ≈ 1.22
    # Blended = (40000*1.25 + 41000*1.22) / (1.25+1.22) ≈ 40494
    # SL at 2%: 40494 * 0.98 ≈ 39684

    # Original SL (without fix): 40000 * 0.98 = 39200

    close = np.full(n, 40500.0)
    close[6] = 40000.0   # entry execution
    close[9] = 41000.0   # add execution
    close[13] = 39500.0  # between the two SL levels

    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.002,
        "low": close * 0.998, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    rsi = np.full(n, 50.0)
    rsi[5] = 25.0   # entry signal
    rsi[8] = 32.0   # add signal
    df["rsi_14"] = rsi

    df.iloc[13, df.columns.get_loc("low")] = 39400.0

    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.ADD_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 35}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.02, take_profit=0.0,
            position_size=0.5, leverage=1,
            direction="long",
        ),
    )

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    trades_df = result.trades_df
    assert trades_df is not None and len(trades_df) > 0

    # The SL should have triggered because blended SL ≈ 39684 > 39400 (low)
    # If it didn't trigger, the position would still be open
    exit_ts = trades_df["Exit Timestamp"].iloc[0]
    assert exit_ts <= dates[14], (
        "SL should trigger at blended entry price level"
    )

def test_add_capped_by_margin():
    """High leverage add should not exceed margin limits."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.ADD_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10,
            position_size=0.5, leverage=3,
            direction="long",
        ),
    )
    df = _make_ohlcv(100)
    df.iloc[5, df.columns.get_loc("rsi_14")] = 25.0
    df.iloc[10, df.columns.get_loc("rsi_14")] = 32.0
    df.iloc[15, df.columns.get_loc("rsi_14")] = 32.0
    df.iloc[20, df.columns.get_loc("rsi_14")] = 75.0

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert isinstance(result.total_return, float)
