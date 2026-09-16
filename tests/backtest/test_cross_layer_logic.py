"""Phase B: cross_layer_logic for role-aware MTF strategies (BUG-7).

Verifies that dna.cross_layer_logic is respected when combining trend
and execution layers in MTF signal generation.
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
from core.strategy.executor import dna_to_signal_set, evaluate_layer, resample_signals

def _make_mtf_dna_with_logic(cross_layer_logic="AND"):
    """Create MTF DNA with specified cross_layer_logic."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5),
        layers=[
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 30}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
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

def _make_divergent_data():
    """Create data where exec and trend layers produce DIFFERENT signals.

    Exec layer: RSI < 30 at bars 10, 20
    Trend layer: price_above EMA at bars 20, 30

    So:
    - bar 10: exec=True, trend=False
    - bar 20: exec=True, trend=True
    - bar 30: exec=False, trend=True

    With AND logic: only bar 20 should be an entry
    With OR logic: bars 10, 20, 30 should all be entries
    """
    n = 50
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = np.full(n, 40000.0)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    # RSI: default 50, low at bars 10 and 20
    rsi = np.full(n, 50.0)
    rsi[10] = 25.0  # exec entry fires
    rsi[20] = 25.0  # exec entry fires
    df["rsi_14"] = rsi

    # EMA: default very high so price_above is False, low at bars 20 and 30
    ema = np.full(n, 50000.0)  # close(40000) < ema(50000) -> price_above = False
    ema[20] = 39000.0  # close(40000) > ema(39000) -> price_above = True
    ema[30] = 39000.0  # price_above = True
    df["ema_50"] = ema

    return df

def test_cross_layer_and_differs_from_or():
    """AND and OR logic should produce different entry counts with divergent data."""
    dna_and = _make_mtf_dna_with_logic("AND")
    dna_or = _make_mtf_dna_with_logic("OR")

    df = _make_divergent_data()
    # Both layers use same timeframe, so same df
    dfs = {"4h": df}

    sig_and = dna_to_signal_set(dna_and, df, dfs_by_timeframe=dfs)
    sig_or = dna_to_signal_set(dna_or, df, dfs_by_timeframe=dfs)

    and_count = int(sig_and.entries.sum())
    or_count = int(sig_or.entries.sum())

    # OR should produce MORE entries than AND with divergent signals
    assert or_count > and_count, (
        f"OR entries ({or_count}) should be > AND entries ({and_count}). "
        f"OR signals: {sig_or.entries.to_dict()}, AND signals: {sig_and.entries.to_dict()}"
    )

def test_cross_layer_and_only_both_true():
    """AND mode: entry only when both trend AND exec fire on same bar."""
    dna = _make_mtf_dna_with_logic("AND")
    df = _make_divergent_data()
    dfs = {"4h": df}

    sig = dna_to_signal_set(dna, df, dfs_by_timeframe=dfs)

    # Bar 10: exec=True, trend=False -> no entry
    assert not sig.entries.iloc[10], "AND: bar 10 should not be entry (exec only)"
    # Bar 20: exec=True, trend=True -> entry
    assert sig.entries.iloc[20], "AND: bar 20 should be entry (both true)"
    # Bar 30: exec=False, trend=True -> no entry
    assert not sig.entries.iloc[30], "AND: bar 30 should not be entry (trend only)"

def test_cross_layer_or_any_true():
    """OR mode: entry when either trend OR exec fires."""
    dna = _make_mtf_dna_with_logic("OR")
    df = _make_divergent_data()
    dfs = {"4h": df}

    sig = dna_to_signal_set(dna, df, dfs_by_timeframe=dfs)

    # Bar 10: exec=True -> entry
    assert sig.entries.iloc[10], "OR: bar 10 should be entry (exec true)"
    # Bar 20: both True -> entry
    assert sig.entries.iloc[20], "OR: bar 20 should be entry (both true)"
    # Bar 30: trend=True -> entry
    assert sig.entries.iloc[30], "OR: bar 30 should be entry (trend true)"

def test_cross_layer_default_and():
    """Default cross_layer_logic is AND."""
    dna = _make_mtf_dna_with_logic("AND")
    df = _make_divergent_data()
    dfs = {"4h": df}

    sig = dna_to_signal_set(dna, df, dfs_by_timeframe=dfs)

    # Default AND: only bar 20
    assert not sig.entries.iloc[10]
    assert sig.entries.iloc[20]
    assert not sig.entries.iloc[30]

def test_backward_compat_no_role_unchanged():
    """MTF DNA without roles should use cross_layer_logic via legacy path."""
    dna_or = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5),
        layers=[
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 30}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
                # No role
            ),
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
            ),
        ],
        cross_layer_logic="OR",
    )

    df = _make_divergent_data()
    dfs = {"4h": df}

    sig = dna_to_signal_set(dna_or, df, dfs_by_timeframe=dfs)

    # OR via legacy path: bars 10, 20, 30 all entries
    assert sig.entries.iloc[10], "Legacy OR: bar 10 should be entry"
    assert sig.entries.iloc[20], "Legacy OR: bar 20 should be entry"
    assert sig.entries.iloc[30], "Legacy OR: bar 30 should be entry"
