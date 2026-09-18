"""Phase A: Eliminate double signal evaluation for MTF strategies (BUG-6).

Verifies that BacktestEngine.run() calls dna_to_signal_set exactly once for
MTF strategies, not twice.
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
import core.backtest.engine as engine_mod
import core.strategy.executor as executor_mod

def _make_ohlcv(n=200, timeframe="4h", seed=42):
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

def _make_mtf_dna():
    """Create MTF DNA with trend+execution layers."""
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
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
                role="trend",
            ),
        ],
    )

def _make_single_tf_dna():
    """Create single-timeframe DNA."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5),
    )

def _patch_and_count(original_fn):
    """Create a counting wrapper and patch both engine and executor modules."""
    call_count = {"n": 0}

    def counting_fn(dna_arg, df_arg, dfs_by_timeframe=None):
        call_count["n"] += 1
        return original_fn(dna_arg, df_arg, dfs_by_timeframe=dfs_by_timeframe)

    # Patch both the engine module's local reference AND executor module
    engine_ref = engine_mod.dna_to_signal_set
    executor_ref = executor_mod.dna_to_signal_set
    engine_mod.dna_to_signal_set = counting_fn
    executor_mod.dna_to_signal_set = counting_fn

    return call_count, engine_ref, executor_ref

def _restore(engine_ref, executor_ref):
    engine_mod.dna_to_signal_set = engine_ref
    executor_mod.dna_to_signal_set = executor_ref

def test_mtf_signal_evaluated_once():
    """MTF strategy run() should call dna_to_signal_set exactly once."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    daily_df = _make_ohlcv(50, "1d")
    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    call_count, eng_ref, exec_ref = _patch_and_count(dna_to_signal_set)
    try:
        engine = BacktestEngine()
        engine.run(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
        assert call_count["n"] == 1, (
            f"Expected dna_to_signal_set called once, got {call_count['n']}"
        )
    finally:
        _restore(eng_ref, exec_ref)

def test_single_tf_signal_evaluated_once():
    """Single-timeframe strategy run() should also call dna_to_signal_set exactly once."""
    dna = _make_single_tf_dna()
    enhanced_df = _make_ohlcv(200, "4h")

    call_count, eng_ref, exec_ref = _patch_and_count(dna_to_signal_set)
    try:
        engine = BacktestEngine()
        engine.run(dna, enhanced_df)
        assert call_count["n"] == 1, (
            f"Expected dna_to_signal_set called once, got {call_count['n']}"
        )
    finally:
        _restore(eng_ref, exec_ref)

def test_degraded_layers_still_reported():
    """After fix, degraded_layers should still be correctly reported in result."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    dfs_by_timeframe = {"4h": enhanced_df}  # Missing 1d -> 1 degraded

    engine = BacktestEngine()
    result = engine.run(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert result.degraded_layers == 1, (
        f"Expected 1 degraded layer, got {result.degraded_layers}"
    )

def test_signal_set_provided_skips_eval():
    """When signal_set is provided, dna_to_signal_set should not be called at all."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv(200, "4h")
    daily_df = _make_ohlcv(50, "1d")

    sig_set = dna_to_signal_set(
        dna, enhanced_df, dfs_by_timeframe={"4h": enhanced_df, "1d": daily_df}
    )

    call_count, eng_ref, exec_ref = _patch_and_count(dna_to_signal_set)
    try:
        engine = BacktestEngine()
        engine.run(dna, enhanced_df, signal_set=sig_set)
        assert call_count["n"] == 0, (
            f"Expected no calls to dna_to_signal_set, got {call_count['n']}"
        )
    finally:
        _restore(eng_ref, exec_ref)
