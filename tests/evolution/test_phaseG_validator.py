"""Phase G: Validator enhancement for cross_layer_logic and mixed-direction.

Verifies:
- cross_layer_logic must be AND or OR
- mixed direction with layers but no trend layer gets a warning
- Valid MTF DNA passes validation
- Existing DNA validation behavior unchanged
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
    TimeframeLayer,
)
from core.strategy.validator import validate_dna

def _make_signal_gene(role=SignalRole.ENTRY_TRIGGER, indicator="RSI"):
    return SignalGene(
        indicator=indicator,
        params={"period": 14},
        role=role,
        field_name="RSI_14",
        condition={"type": "lt", "threshold": 30},
    )

def _make_risk(direction="long"):
    return RiskGenes(
        stop_loss=0.05, take_profit=0.10, position_size=0.5,
        leverage=1, direction=direction,
    )

def _make_mtf_dna(direction="long", cross_layer_logic="AND", layers=None):
    """Create an MTF DNA with 2 layers by default."""
    entry_gene = SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                            'RSI_14', {'type': 'lt', 'threshold': 30})
    exit_gene = SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER,
                           'RSI_14', {'type': 'gt', 'threshold': 70})
    direction_gene = SignalGene('EMA', {'period': 50}, SignalRole.DIRECTION,
                                None, {'type': 'price_above'})
    if layers is None:
        layers = [
            TimeframeLayer(
                timeframe='4h',
                signal_genes=[entry_gene, exit_gene],
                logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
                role='execution',
            ),
            TimeframeLayer(
                timeframe='1d',
                signal_genes=[entry_gene, exit_gene] +
                    ([direction_gene] if direction == "mixed" else []),
                logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
                role='trend',
            ),
        ]
    return StrategyDNA(
        signal_genes=[entry_gene, exit_gene],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=_make_risk(direction=direction),
        layers=layers,
        cross_layer_logic=cross_layer_logic,
    )

def test_rejects_invalid_cross_layer_logic():
    """cross_layer_logic must be AND or OR, not other values."""
    dna = _make_mtf_dna(cross_layer_logic="XOR")
    result = validate_dna(dna)
    assert not result.is_valid
    assert any("cross_layer_logic" in e.lower() for e in result.errors)

def test_warns_mixed_without_trend_layer():
    """mixed direction with layers but no trend layer should produce a warning.

    This is a warning (not error), so is_valid should still be True,
    but warnings should be present.
    """
    entry_gene = SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                            'RSI_14', {'type': 'lt', 'threshold': 30})
    exit_gene = SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER,
                           'RSI_14', {'type': 'gt', 'threshold': 70})
    direction_gene = SignalGene('EMA', {'period': 50}, SignalRole.DIRECTION,
                                None, {'type': 'price_above'})
    layers = [
        TimeframeLayer(
            timeframe='4h',
            signal_genes=[entry_gene, exit_gene, direction_gene],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
            role='execution',
        ),
        TimeframeLayer(
            timeframe='1h',
            signal_genes=[entry_gene, exit_gene],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
            role='execution',
        ),
    ]
    dna = _make_mtf_dna(direction="mixed", layers=layers)
    result = validate_dna(dna)
    # Warning doesn't make it invalid, but should be flagged
    assert result.is_valid
    assert any("mixed" in e.lower() and "trend" in e.lower() for e in result.warnings)

def test_valid_mtf_with_trend_passes():
    """Valid MTF DNA with mixed direction and trend layer should pass."""
    dna = _make_mtf_dna(direction="mixed")
    result = validate_dna(dna)
    assert result.is_valid
    assert len(result.errors) == 0

def test_backward_compat_existing_dna():
    """Existing non-MTF DNA should validate the same as before."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                        'RSI_14', {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER,
                        'RSI_14', {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=_make_risk(),
    )
    result = validate_dna(dna)
    assert result.is_valid
    assert len(result.errors) == 0
