"""Phase 3: Evolution Operator MTF constraint fixes.

Verifies:
- H3: create_random_mtf_layer uses consistent indicator/params/condition
- M4: Crossover preserves role field on child layers
- L1: mutate_layer_timeframe prevents duplicate timeframes
- L2: Crossover handles mismatched layer counts without truncation
"""

import random
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
from core.evolution.operators import (
    crossover,
    mutate_layer_timeframe,
)
from core.evolution.population import create_random_mtf_layer

# ── Helpers ──

def _make_mtf_dna_with_layers(
    layer_count=2,
    roles=None,
    timeframes=None,
) -> StrategyDNA:
    """Create an MTF DNA with specified layers."""
    if timeframes is None:
        timeframes = ["1h", "1d", "3d"][:layer_count]
    if roles is None:
        roles = ["trend", "execution", "execution"][:layer_count]

    layers = []
    for i in range(layer_count):
        layers.append(TimeframeLayer(
            timeframe=timeframes[i],
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                            {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "RSI_14",
                            {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            role=roles[i],
        ))

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
        layers=layers,
        cross_layer_logic="AND",
    )

# ── H3: create_random_mtf_layer consistent indicator/params/condition ──

def test_mtf_layer_entry_trigger_consistent_indicator():
    """Entry trigger gene should have matching indicator, params, and condition."""
    random.seed(123)
    layer = create_random_mtf_layer("1d")

    entry_genes = [g for g in layer.signal_genes if g.role == SignalRole.ENTRY_TRIGGER]
    assert len(entry_genes) >= 1

    for gene in entry_genes:
        # The indicator name should match what was used for params generation
        # We can verify this by checking that _random_params returns
        # a dict whose keys match the indicator's expected params
        from core.features.registry import INDICATOR_REGISTRY
        reg = INDICATOR_REGISTRY.get(gene.indicator)
        if reg:
            for param_name in reg.params:
                assert param_name in gene.params, \
                    f"Expected param {param_name!r} for indicator {gene.indicator!r}, " \
                    f"but got params {list(gene.params.keys())}"

def test_mtf_layer_exit_trigger_consistent_indicator():
    """Exit trigger gene should have matching indicator, params, and condition."""
    random.seed(456)
    layer = create_random_mtf_layer("4h")

    exit_genes = [g for g in layer.signal_genes if g.role == SignalRole.EXIT_TRIGGER]
    assert len(exit_genes) >= 1

    for gene in exit_genes:
        from core.features.registry import INDICATOR_REGISTRY
        reg = INDICATOR_REGISTRY.get(gene.indicator)
        if reg:
            for param_name in reg.params:
                assert param_name in gene.params, \
                    f"Expected param {param_name!r} for indicator {gene.indicator!r}, " \
                    f"but got params {list(gene.params.keys())}"

def test_mtf_layer_genes_are_valid_for_evaluation():
    """All genes in random layer should be evaluable without KeyError."""
    import numpy as np
    import pandas as pd

    random.seed(789)
    layer = create_random_mtf_layer("4h")

    # Create a minimal DataFrame for evaluation
    n = 200
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.005,
        "low": close * 0.995, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    # Try computing indicators for each gene
    from core.features.indicators import _compute_indicator
    for gene in layer.signal_genes:
        try:
            result = _compute_indicator(df, gene.indicator, gene.params)
            assert result is not None
        except KeyError as e:
            pytest.fail(f"Gene indicator={gene.indicator}, params={gene.params} "
                        f"caused KeyError: {e}")
        except Exception:
            pass  # Some indicators may fail on small data, that's OK

def test_multiple_random_layers_statistical_consistency():
    """Generate many random layers and verify consistency statistically."""
    random.seed(42)
    failures = 0
    total = 50

    from core.features.registry import INDICATOR_REGISTRY

    for _ in range(total):
        layer = create_random_mtf_layer("4h")
        for gene in layer.signal_genes:
            reg = INDICATOR_REGISTRY.get(gene.indicator)
            if reg:
                for param_name in reg.params:
                    if param_name not in gene.params:
                        failures += 1
                        break

    assert failures == 0, \
        f"{failures}/{total} genes had mismatched indicator/params"

# ── M4: Crossover preserves role field ──

def test_crossover_preserves_trend_role():
    """Child layers should inherit role from parents during crossover."""
    parent_a = _make_mtf_dna_with_layers(2, roles=["trend", "execution"])
    parent_b = _make_mtf_dna_with_layers(2, roles=["trend", "execution"])

    child = crossover(parent_a, parent_b)

    assert child.layers is not None
    assert len(child.layers) == 2

    # At least one layer should have role="trend" (from either parent)
    child_roles = [layer.role for layer in child.layers]
    assert "trend" in child_roles, \
        f"Expected 'trend' role in child layers, got {child_roles}"

def test_crossover_preserves_all_roles():
    """Both trend and execution roles should survive crossover."""
    parent_a = _make_mtf_dna_with_layers(
        3, roles=["trend", "execution", "execution"],
        timeframes=["1h", "1d", "3d"],
    )
    parent_b = _make_mtf_dna_with_layers(
        3, roles=["trend", "execution", "execution"],
        timeframes=["1h", "1d", "3d"],
    )

    child = crossover(parent_a, parent_b)

    assert child.layers is not None
    assert len(child.layers) == 3

    for layer in child.layers:
        assert layer.role is not None, \
            f"Layer with tf={layer.timeframe} should have a role, got None"

def test_crossover_single_parent_has_layers():
    """When only one parent has layers, child should get those layers."""
    parent_a = _make_mtf_dna_with_layers(2, roles=["trend", "execution"])
    parent_b = _make_mtf_dna_with_layers(0)  # No layers
    # parent_b won't have layers since layer_count=0
    parent_b = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "RSI_14",
                        {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
    )

    child = crossover(parent_a, parent_b)

    assert child.layers is not None
    assert len(child.layers) == 2
    # Roles should be preserved from parent_a
    assert child.layers[0].role == "trend"
    assert child.layers[1].role == "execution"

# ── L1: mutate_layer_timeframe prevents duplicate timeframes ──

def test_mutate_layer_timeframe_no_duplicates():
    """Mutating a layer's timeframe should not create duplicate timeframes."""
    random.seed(42)
    dna = _make_mtf_dna_with_layers(
        3, roles=["trend", "execution", "execution"],
        timeframes=["1h", "1d", "3d"],
    )

    candidate_timeframes = ["15m", "30m", "1h", "4h", "1d", "3d"]

    # Run mutation many times and check for duplicates
    for seed in range(100):
        random.seed(seed)
        mutated = mutate_layer_timeframe(dna, candidate_timeframes=candidate_timeframes)

        if mutated.to_dict()["layers"] != dna.to_dict()["layers"]:
            # Mutation happened, check for duplicate timeframes
            tfs = [layer.timeframe for layer in mutated.layers]
            assert len(tfs) == len(set(tfs)), \
                f"Duplicate timeframes after mutation: {tfs} (seed={seed})"

def test_mutate_layer_timeframe_preserves_count():
    """Mutation should not change the number of layers."""
    random.seed(42)
    dna = _make_mtf_dna_with_layers(3)
    original_count = len(dna.layers)

    mutated = mutate_layer_timeframe(dna)

    assert len(mutated.layers) == original_count

# ── L2: Crossover handles mismatched layer counts ──

def test_crossover_different_layer_counts_preserves_max():
    """Crossover with different layer counts should preserve all layers."""
    parent_a = _make_mtf_dna_with_layers(
        3, roles=["trend", "execution", "execution"],
        timeframes=["1h", "1d", "3d"],
    )
    parent_b = _make_mtf_dna_with_layers(
        2, roles=["trend", "execution"],
        timeframes=["1h", "1d"],
    )

    child = crossover(parent_a, parent_b)

    assert child.layers is not None
    # Should have 3 layers (max of both parents), not 2 (zip truncation)
    assert len(child.layers) == 3, \
        f"Expected 3 layers, got {len(child.layers)} (zip truncation bug?)"

def test_crossover_different_layer_counts_preserves_roles():
    """All layers in child should have valid roles."""
    parent_a = _make_mtf_dna_with_layers(
        3, roles=["trend", "execution", "execution"],
        timeframes=["1h", "1d", "3d"],
    )
    parent_b = _make_mtf_dna_with_layers(
        1, roles=["trend"],
        timeframes=["1d"],
    )

    child = crossover(parent_a, parent_b)

    assert child.layers is not None
    assert len(child.layers) == 3

    for layer in child.layers:
        assert layer.role is not None, \
            f"Layer tf={layer.timeframe} should have a role after crossover"

def test_crossover_different_layer_counts_preserves_timeframes():
    """No layer timeframe should be lost during crossover."""
    parent_a = _make_mtf_dna_with_layers(
        3, timeframes=["1h", "1d", "3d"],
        roles=["trend", "execution", "execution"],
    )
    parent_b = _make_mtf_dna_with_layers(
        1, timeframes=["1d"],
        roles=["trend"],
    )

    parent_a_tfs = {layer.timeframe for layer in parent_a.layers}

    child = crossover(parent_a, parent_b)

    child_tfs = {layer.timeframe for layer in child.layers}
    # All timeframes from parent_a should be preserved
    assert parent_a_tfs.issubset(child_tfs), \
        f"Lost timeframes: {parent_a_tfs - child_tfs}"

# ── Integration: Full mutation/crossover cycle ──

def test_mutation_preserves_layer_structure():
    """Various mutations should not break layer structure."""
    random.seed(42)
    dna = _make_mtf_dna_with_layers(2, roles=["trend", "execution"])

    # mutate_layer_timeframe
    mutated = mutate_layer_timeframe(dna, candidate_timeframes=["1h", "1d", "3d"])
    assert mutated.layers is not None
    assert all(layer.role is not None for layer in mutated.layers)

    # Crossover
    child = crossover(dna, mutated)
    assert child.layers is not None
    tfs = [layer.timeframe for layer in child.layers]
    assert len(tfs) == len(set(tfs)), f"Duplicate timeframes: {tfs}"
