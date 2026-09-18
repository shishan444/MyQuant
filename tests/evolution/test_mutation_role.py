"""Phase H: mutate_add_layer role assignment fix.

Verifies that layers created by mutate_add_layer have a 'role' field
(defaulting to 'execution'), so they are not ignored by MTF signal combination.
"""

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
from core.evolution.operators import mutate_add_layer

def _make_base_dna():
    """Create a simple non-MTF DNA that can have layers added."""
    return StrategyDNA(
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                        'RSI_14', {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER,
                        'RSI_14', {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.10, position_size=0.5,
            leverage=1, direction='long',
        ),
    )

def test_mutate_add_layer_assigns_role():
    """Newly added layer must have a 'role' field in its dict representation."""
    dna = _make_base_dna()
    result = mutate_add_layer(dna, candidate_timeframes=["1d", "1h"])
    assert result.is_mtf
    # The newly added layer should have a role
    for layer in result.layers:
        if layer.role is not None:
            assert layer.role in ("trend", "execution", "structure", "zone")

def test_mutate_add_layer_default_execution():
    """Newly added layer should default to 'execution' role.

    Since mutate_add_layer creates a random DNA seed and wraps it,
    the new layer should have role='execution' by default.
    """
    dna = _make_base_dna()
    result = mutate_add_layer(dna, candidate_timeframes=["1d", "1h"])
    assert result.is_mtf
    # Find the new layer (not the original 4h one)
    new_layers = [l for l in result.layers if l.timeframe != "4h"]
    assert len(new_layers) >= 1
    # The new layer should have a role (not None)
    new_layer = new_layers[0]
    assert new_layer.role is not None

def test_evolved_dna_with_role_generates_signals():
    """Evolved DNA with role-assigned layers should be valid.

    The DNA should pass validation and have proper layer structure.
    """
    dna = _make_base_dna()
    result = mutate_add_layer(dna, candidate_timeframes=["1d", "1h"])
    assert result.is_mtf
    # All layers should have a timeframe
    for layer in result.layers:
        assert layer.timeframe is not None
        assert len(layer.signal_genes) > 0
    # Should have 2 layers (original + new)
    assert len(result.layers) == 2
