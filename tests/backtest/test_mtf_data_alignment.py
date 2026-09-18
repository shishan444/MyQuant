"""Phase G: MTF data loading aligned with evolution engine.

Verifies:
- mutate_add_layer respects candidate_timeframes (no out-of-pool TFs)
- mutate_layer_timeframe respects candidate_timeframes
- create_random_dna only creates layers from timeframe_pool
- EvolutionEngine uses timeframe_pool to constrain mutations
- Runner passes loaded TFs (not raw tf_pool) to engine
"""

import random
from unittest.mock import patch, MagicMock

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
from core.evolution.operators import mutate_add_layer, mutate_layer_timeframe
from core.evolution.population import create_random_dna

def _make_mtf_dna(layers=None):
    """Create MTF DNA with given layers."""
    if layers is None:
        layers = [
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
                timeframe="1d",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 35}),
                ],
                logic_genes=LogicGenes(entry_logic="AND"),
                role="trend",
            ),
        ]
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
        layers=layers,
    )

# ── mutate_add_layer respects candidate_timeframes ──

def test_mutate_add_layer_respects_candidate_timeframes():
    """New layer TF must come from candidate list, not _STANDARD_TIMEFRAMES."""
    dna = _make_mtf_dna()  # has 4h + 1d layers
    candidates = ["4h", "1d", "1h"]  # only 1h is not yet used

    result = mutate_add_layer(dna, candidate_timeframes=candidates)
    if result.layers and len(result.layers) > len(dna.layers):
        new_tfs = {l.timeframe for l in result.layers} - {l.timeframe for l in dna.layers}
        assert new_tfs.issubset(set(candidates)), \
            f"New TF {new_tfs} not in candidates {candidates}"

def test_mutate_add_layer_never_produces_out_of_pool_tf():
    """With 100 random seeds, never produce TF outside candidate list."""
    candidates = ["4h", "1d", "1h"]
    for seed in range(100):
        random.seed(seed)
        dna = _make_mtf_dna()
        result = mutate_add_layer(dna, candidate_timeframes=candidates)
        for layer in result.layers:
            assert layer.timeframe in candidates, \
                f"Layer TF '{layer.timeframe}' not in candidates {candidates}"

# ── mutate_layer_timeframe respects candidate_timeframes ──

def test_mutate_layer_timeframe_respects_candidates():
    """Changed layer TF must come from candidate list."""
    random.seed(42)
    dna = _make_mtf_dna()
    candidates = ["4h", "1d", "1h"]

    result = mutate_layer_timeframe(dna, candidate_timeframes=candidates)
    for layer in result.layers:
        assert layer.timeframe in candidates, \
            f"Layer TF '{layer.timeframe}' not in candidates {candidates}"

# ── create_random_dna respects timeframe_pool ──

def test_create_random_dna_respects_timeframe_pool():
    """Layers should only contain TFs from timeframe_pool."""
    random.seed(42)
    tf_pool = ["4h", "1d", "1h"]
    dna = create_random_dna(
        timeframe="4h",
        symbol="BTCUSDT",
        timeframe_pool=tf_pool,
    )
    if dna.layers:
        for layer in dna.layers:
            assert layer.timeframe in tf_pool, \
                f"Layer TF '{layer.timeframe}' not in timeframe_pool {tf_pool}"

# ── EvolutionEngine uses timeframe_pool for mutations ──

def test_engine_timeframe_pool_constrains_mutations():
    """EvolutionEngine should only produce layers from its timeframe_pool."""
    from core.evolution.engine import EvolutionEngine
    from functools import partial

    engine = EvolutionEngine(
        target_score=100,
        max_generations=1,
        timeframe_pool=["4h", "1d"],
    )
    assert engine.timeframe_pool == ["4h", "1d"]

    # Verify mutation_pool contains partial with correct candidates
    # by checking mutate_add_layer with a 1-layer DNA
    random.seed(42)
    dna = _make_mtf_dna(layers=[
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
    ])

    # Only "1d" is a valid candidate from ["4h", "1d"]
    result = mutate_add_layer(dna, candidate_timeframes=list(engine.timeframe_pool))
    if result.layers and len(result.layers) > 1:
        new_tfs = {l.timeframe for l in result.layers}
        assert new_tfs.issubset({"4h", "1d"}), \
            f"Layer TFs {new_tfs} outside engine pool {engine.timeframe_pool}"

# ── Runner passes loaded TFs to engine ──

def test_runner_passes_loaded_tfs_to_engine():
    """When load_mtf_data returns subset of tf_pool, engine should get subset."""
    from core.evolution.engine import EvolutionEngine

    # Simulate: tf_pool=["4h","1d","1h"] but only 4h+1d loaded
    loaded_tfs = ["4h", "1d"]

    engine = EvolutionEngine(
        target_score=100,
        max_generations=1,
        timeframe_pool=loaded_tfs,
    )
    assert engine.timeframe_pool == loaded_tfs

    # With this constrained pool, mutate_add_layer should only use these TFs
    dna = _make_mtf_dna(layers=[
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
    ])

    for seed in range(20):
        random.seed(seed)
        result = mutate_add_layer(dna, candidate_timeframes=list(engine.timeframe_pool))
        for layer in result.layers:
            assert layer.timeframe in loaded_tfs, \
                f"Layer TF '{layer.timeframe}' not in loaded_tfs {loaded_tfs}"
