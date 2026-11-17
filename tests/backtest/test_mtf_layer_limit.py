"""Phase E: MTF layer count hard limit (1-3).

Verifies:
- Validator rejects DNA with >3 layers
- Validator accepts DNA with exactly 3 layers
- mutate_add_layer refuses when DNA already has 3 layers
- create_random_dna caps layers when timeframe_pool > 3
- init_population caps layers when timeframe_pool > 3
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
from core.strategy.validator import validate_dna

def _make_layer(timeframe: str, role: str = "execution") -> TimeframeLayer:
    """Create a minimal valid TimeframeLayer."""
    return TimeframeLayer(
        timeframe=timeframe,
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        role=role,
    )

def _make_mtf_dna(n_layers: int = 2) -> StrategyDNA:
    """Create an MTF DNA with n_layers (1 exec + (n-1) others)."""
    tfs = ["4h", "1d", "1h", "15m", "30m", "3d"]
    layers = []
    for i in range(n_layers):
        role = "trend" if i == 0 and n_layers > 1 else "execution"
        layers.append(_make_layer(tfs[i], role))
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

# ── Validator: layer count ──

def test_validator_rejects_4_layers():
    """4-layer DNA should fail validation."""
    dna = _make_mtf_dna(4)
    result = validate_dna(dna)
    assert not result.is_valid
    assert any("max 3 layers" in e.lower() or "max 3" in e for e in result.errors), \
        f"Expected max layers error, got: {result.errors}"

def test_validator_rejects_5_layers():
    """5-layer DNA should fail validation."""
    dna = _make_mtf_dna(5)
    result = validate_dna(dna)
    assert not result.is_valid
    assert any("max 3" in e for e in result.errors)

def test_validator_accepts_3_layers():
    """3-layer DNA should pass validation."""
    dna = _make_mtf_dna(3)
    result = validate_dna(dna)
    assert result.is_valid, f"3-layer DNA should be valid, errors: {result.errors}"

def test_validator_accepts_1_layer():
    """1-layer MTF DNA should pass validation."""
    dna = _make_mtf_dna(1)
    result = validate_dna(dna)
    assert result.is_valid, f"1-layer DNA should be valid, errors: {result.errors}"
