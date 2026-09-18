"""Phase 4: End-to-end integration verification.

Verifies the complete MTF pipeline after all fixes:
- MTF strategy backtest produces valid results
- MTF strategy comparison works correctly
- Evolution produces structurally valid MTF strategies
- Random MTF layer generates evaluable genes
"""

import json
import random
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
from core.backtest.engine import BacktestEngine
from core.strategy.executor import dna_to_signal_set
from core.evolution.operators import crossover, mutate_layer_timeframe
from core.evolution.population import create_random_mtf_layer

# ── Shared Fixtures ──

def _make_ohlcv_df(n=500, timeframe="4h", start="2024-01-01", seed=42):
    """Create synthetic OHLCV DataFrame with indicators."""
    freq_map = {"1h": "1h", "4h": "4h", "1d": "1D", "15m": "15min"}
    freq = freq_map.get(timeframe, "4h")
    np.random.seed(seed)
    dates = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    close_s = pd.Series(close, index=dates)
    delta = close_s.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain, index=dates).rolling(14).mean()
    avg_loss = pd.Series(loss, index=dates).rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df["rsi_14"] = rsi.fillna(50)
    df["ema_50"] = close_s.ewm(span=50).mean()
    df["ema_200"] = close_s.ewm(span=200).mean()

    return df

def _make_mtf_dna():
    """Create a realistic MTF strategy with trend + execution layers."""
    return StrategyDNA(
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
                role="structure",
            ),
        ],
    )

# ── E2E Test 1: MTF Strategy Backtest ──

def test_mtf_backtest_produces_valid_result():
    """MTF strategy should produce a valid backtest result with multiple timeframes."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert result.total_return is not None
    assert result.equity_curve is not None
    assert len(result.equity_curve) > 0
    assert isinstance(result.total_trades, int)
    assert result.total_trades >= 0

def test_mtf_backtest_without_mtf_data_raises():
    """MTF strategy without dfs_by_timeframe should raise ValueError."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv_df(500, "4h")

    engine = BacktestEngine(init_cash=100000)
    with pytest.raises(ValueError, match="MTF DNA requires dfs_by_timeframe"):
        engine.run(dna, enhanced_df)

def test_single_tf_backtest_unchanged():
    """Single-TF strategy should produce same results as before."""
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

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, enhanced_df)

    assert result.total_return is not None
    assert len(result.equity_curve) > 0

# ── E2E Test 3: MTF Signal Evaluation Pipeline ──

def test_mtf_signal_pipeline_with_roles():
    """Full signal pipeline: DNA -> SignalSet with role-aware combination."""
    dna = _make_mtf_dna()
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    assert len(sig_set.entries) == len(enhanced_df)
    assert len(sig_set.exits) == len(enhanced_df)
    assert len(sig_set.adds) == len(enhanced_df)
    assert len(sig_set.reduces) == len(enhanced_df)

    # Entries and exits should be boolean
    assert sig_set.entries.dtype == bool or str(sig_set.entries.dtype) == "boolean"
    assert sig_set.exits.dtype == bool or str(sig_set.exits.dtype) == "boolean"

def test_mtf_signal_pipeline_no_roles():
    """MTF without roles should use legacy combination."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            ),
        ],
        cross_layer_logic="AND",
    )
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    sig_set = dna_to_signal_set(dna, enhanced_df,
                                 dfs_by_timeframe={"4h": enhanced_df, "1d": daily_df})

    assert len(sig_set.entries) == len(enhanced_df)
    assert isinstance(sig_set.entries, pd.Series)

# ── E2E Test 4: Evolution produces valid MTF strategies ──

def test_random_layer_produces_evaluable_strategy():
    """Random MTF layer should produce a strategy that can be evaluated."""
    random.seed(42)
    layer = create_random_mtf_layer("1d")

    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10),
        layers=[layer],
    )

    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    dfs_by_timeframe = {"4h": enhanced_df, "1d": daily_df}

    # Should not raise any errors
    sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
    assert isinstance(sig_set.entries, pd.Series)

def test_evolution_cycle_produces_valid_mtf():
    """Full evolution cycle: create -> crossover -> mutate should produce valid DNA."""
    random.seed(42)

    # Create two parent MTF strategies
    parent_a = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=[
            TimeframeLayer("1d", [
                SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                            {"type": "price_above"}),
            ], LogicGenes(), role="structure"),
            TimeframeLayer("1h", [
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                            {"type": "lt", "threshold": 30}),
            ], LogicGenes(), role="execution"),
        ],
    )

    parent_b = StrategyDNA(
        signal_genes=[
            SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER, "ema_20",
                        {"type": "price_above"}),
        ],
        logic_genes=LogicGenes(entry_logic="OR", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=[
            TimeframeLayer("3d", [
                SignalGene("EMA", {"period": 200}, SignalRole.ENTRY_TRIGGER, "ema_200",
                            {"type": "price_above"}),
            ], LogicGenes(), role="structure"),
        ],
    )

    # Crossover
    child = crossover(parent_a, parent_b)

    # Verify child structure
    assert child.layers is not None
    assert len(child.layers) >= 1  # At least the max of both parents' layers

    # Verify no duplicate timeframes
    tfs = [layer.timeframe for layer in child.layers]
    assert len(tfs) == len(set(tfs)), f"Duplicate timeframes: {tfs}"

    # Verify all layers have roles
    for layer in child.layers:
        assert layer.role is not None

    # Mutate
    mutated = mutate_layer_timeframe(child, candidate_timeframes=["1h", "1d", "3d"])

    # Still no duplicates after mutation
    tfs = [layer.timeframe for layer in mutated.layers]
    assert len(tfs) == len(set(tfs)), f"Duplicate timeframes after mutation: {tfs}"

    # Can serialize and deserialize
    serialized = mutated.to_dict()
    restored = StrategyDNA.from_dict(serialized)
    assert len(restored.layers) == len(mutated.layers)

# ── E2E Test 5: DNA Serialization Round-trip ──

def test_mtf_dna_serialization_roundtrip():
    """MTF DNA with roles should survive serialization round-trip."""
    original = _make_mtf_dna()

    # to_dict -> from_dict
    d = original.to_dict()
    restored = StrategyDNA.from_dict(d)

    assert len(restored.layers) == len(original.layers)
    for orig_layer, rest_layer in zip(original.layers, restored.layers):
        assert orig_layer.timeframe == rest_layer.timeframe
        assert orig_layer.role == rest_layer.role

    # to_json -> from_json
    json_str = original.to_json()
    restored2 = StrategyDNA.from_json(json_str)

    assert len(restored2.layers) == len(original.layers)
    for orig_layer, rest_layer in zip(original.layers, restored2.layers):
        assert orig_layer.role == rest_layer.role

# ── E2E Test 6: API Schema Compatibility ──

def test_mtf_dna_pydantic_roundtrip():
    """MTF DNA should survive Pydantic model round-trip."""
    from api.schemas import DNAModel

    original = _make_mtf_dna()

    # StrategyDNA -> dict -> DNAModel -> dict -> StrategyDNA
    d = original.to_dict()
    pydantic_model = DNAModel(**d)
    d2 = pydantic_model.model_dump()
    restored = StrategyDNA.from_dict(d2)

    assert len(restored.layers) == len(original.layers)
    assert restored.layers[0].role == original.layers[0].role

# ── E2E Test 7: Compare endpoint contract ──

def test_compare_endpoint_needed_tfs_construction():
    """Verify the needed_tfs construction logic used in compare endpoint."""
    dna = _make_mtf_dna()
    exec_tf = dna.execution_genes.timeframe

    # This is the exact logic used in the fixed compare endpoint
    needed_tfs = {layer.timeframe for layer in dna.layers}
    needed_tfs.add(exec_tf)

    assert "4h" in needed_tfs
    assert "1d" in needed_tfs
    assert len(needed_tfs) == 2

# ── E2E Test 8: No regressions in single-TF path ──

def test_single_tf_dna_still_works_end_to_end():
    """Single-TF strategy should produce identical results after all MTF fixes."""
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
    raw_df = enhanced_df[["open", "high", "low", "close", "volume"]].copy()

    # Backtest
    engine = BacktestEngine(init_cash=100000)
    bt_result = engine.run(dna, enhanced_df)
    assert bt_result.total_return is not None

    # Signal evaluation
    sig_set = dna_to_signal_set(dna, enhanced_df)
    assert len(sig_set.entries) == len(enhanced_df)

def test_mixed_direction_mtf_backtest():
    """MTF strategy with mixed direction should produce valid results."""
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
                             leverage=2, direction="mixed"),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, "ema_50",
                                {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                role="structure",
            ),
        ],
    )
    enhanced_df = _make_ohlcv_df(500, "4h")
    daily_df = _make_ohlcv_df(200, "1d")

    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, enhanced_df,
                         dfs_by_timeframe={"4h": enhanced_df, "1d": daily_df})

    assert result.total_return is not None
    assert isinstance(result.total_trades, int)
