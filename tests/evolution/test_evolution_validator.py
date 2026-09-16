"""Phase D: Evolution role assignment + validator layer checks.

Verifies:
- A4: create_random_mtf_layer assigns role field
- A4: Role distribution includes both trend and execution
- B3: Validator checks condition structures in layer genes
- B3: Validator detects invalid conditions in layer genes
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
from core.evolution.population import create_random_mtf_layer
from core.strategy.validator import validate_dna

# ── A4: create_random_mtf_layer assigns role ──

def test_random_layer_has_role():
    """Every random MTF layer should have a role assigned."""
    random.seed(42)
    for _ in range(50):
        layer = create_random_mtf_layer("1d")
        assert layer.role is not None, "Layer should have a role"
        assert layer.role in ("trend", "execution", "structure", "zone"), \
            f"Role should be 'trend', 'execution', 'structure', or 'zone', got '{layer.role}'"

def test_random_layer_role_distribution():
    """Random layers should produce both structure and execution roles."""
    random.seed(42)
    roles = []
    for _ in range(100):
        layer = create_random_mtf_layer("1d")
        roles.append(layer.role)

    # "structure" is derive_role("1d") which appears ~60% of the time
    struct_count = roles.count("structure") + roles.count("trend")
    exec_count = roles.count("execution")

    assert struct_count > 10, f"Expected some structure layers, got {struct_count}/100"
    assert exec_count > 10, f"Expected some execution layers, got {exec_count}/100"

def test_random_layer_with_dna_produces_valid_strategy():
    """Random layer embedded in DNA should produce evaluable strategy."""
    random.seed(42)
    layer = create_random_mtf_layer("1d")
    # Force at least one execution layer to pass validation
    exec_layer = create_random_mtf_layer("4h")
    # Ensure one is execution
    from core.strategy.dna import TimeframeLayer as TL
    safe_exec = TL(
        timeframe=exec_layer.timeframe,
        signal_genes=exec_layer.signal_genes,
        logic_genes=exec_layer.logic_genes,
        role="execution",
    )
    layers = [layer, safe_exec]

    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10),
        layers=layers,
    )

    # Should pass validation
    result = validate_dna(dna)
    assert result.is_valid, f"DNA with random layer should be valid, errors: {result.errors}"

def test_random_layer_serialization_preserves_role():
    """Random layer role should survive serialization round-trip."""
    random.seed(42)
    layer = create_random_mtf_layer("1d")
    original_role = layer.role

    # Round-trip through DNA serialization
    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=[layer],
    )

    restored = StrategyDNA.from_dict(dna.to_dict())
    # "trend" maps to "structure" on deserialization - both are equivalent
    expected = "structure" if original_role == "trend" else original_role
    assert restored.layers[0].role == expected

# ── B3: Validator checks layer gene conditions ──

def test_validator_checks_layer_condition_structure():
    """Validator should detect invalid conditions in layer genes."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                               {"type": "touch_bounce"}),  # Missing 'direction'
                ],
                logic_genes=LogicGenes(),
                role="trend",
            ),
        ],
    )

    result = validate_dna(dna)
    assert not result.is_valid
    assert any("touch_bounce requires 'direction'" in e for e in result.errors), \
        f"Expected touch_bounce direction error, got: {result.errors}"

def test_validator_checks_layer_lookback_condition():
    """Validator should detect invalid lookback conditions in layer genes."""
    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                               {"type": "lookback_any"}),  # Missing 'window' and 'inner'
                ],
                logic_genes=LogicGenes(),
                role="execution",
            ),
        ],
    )

    result = validate_dna(dna)
    assert not result.is_valid
    assert any("window" in e for e in result.errors), \
        f"Expected lookback window error, got: {result.errors}"

def test_validator_valid_layer_genes_pass():
    """Valid layer genes should pass validation."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                        {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5),
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
                role="trend",
            ),
            TimeframeLayer(
                timeframe="1h",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 30}),
                    SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
                                {"type": "gt", "threshold": 70}),
                ],
                logic_genes=LogicGenes(),
                role="execution",
            ),
        ],
    )

    result = validate_dna(dna)
    assert result.is_valid, f"Valid DNA should pass, errors: {result.errors}"

def test_validator_detects_invalid_layer_role():
    """Validator should reject invalid layer roles."""
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                        {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
                                {"type": "lt", "threshold": 30}),
                ],
                logic_genes=LogicGenes(),
                role="invalid_role",  # Invalid role
            ),
        ],
    )

    result = validate_dna(dna)
    assert not result.is_valid
    assert any("invalid role" in e for e in result.errors)
