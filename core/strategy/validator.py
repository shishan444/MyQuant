"""DNA validation - checks strategy legality before backtesting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from core.strategy.dna import StrategyDNA, SignalRole

# 止损 pct 值域（0.4.1 R6 提升为模块级常量——exit_sweep 网格校验引用式
# 消费，改域单点生效；原函数内字面等价替换，行为零变化）
SL_PCT_MIN = 0.005
SL_PCT_MAX = 0.20


@dataclass
class ValidationResult:
    """Result of DNA validation."""
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_dna(dna: StrategyDNA) -> ValidationResult:
    """Validate a StrategyDNA for legality.

    Checks:
    - At least 1 entry signal (trigger or guard)
    - At least 1 exit signal (trigger or guard)
    - Stop loss in [0.005, 0.20]
    - Position size in [0.10, 1.0]
    - Take profit > stop loss (if set)
    - Condition structure validity for new condition types
    - cross_layer_logic must be AND or OR (for MTF strategies)
    - Warn if mixed direction with layers but no trend layer
    """
    errors: List[str] = []
    warnings: List[str] = []

    # For non-MTF strategies, check top-level signal_genes
    if not dna.layers:
        entry_signals = [
            g for g in dna.signal_genes
            if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
        ]
        if not entry_signals:
            errors.append("No entry signal defined (need at least one entry_trigger or entry_guard)")

        exit_signals = [
            g for g in dna.signal_genes
            if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)
        ]
        if not exit_signals:
            errors.append("No exit signal defined (need at least one exit_trigger or exit_guard)")

        # Mixed direction requires at least one DIRECTION gene
        if dna.risk_genes.direction == "mixed":
            direction_genes = [
                g for g in dna.signal_genes
                if g.role == SignalRole.DIRECTION
            ]
            if not direction_genes:
                errors.append(
                    "mixed direction requires at least one DIRECTION gene"
                )

    # Also check layers for MTF strategies
    if dna.layers:
        if len(dna.layers) > 3:
            errors.append(
                f"MTF strategy supports max 3 layers, got {len(dna.layers)}"
            )
        has_execution = False
        has_trend = False
        for layer in dna.layers:
            layer_entry = [
                g for g in layer.signal_genes
                if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
            ]
            if not layer_entry:
                errors.append(f"Layer {layer.timeframe}: no entry signal defined")
            layer_exit = [
                g for g in layer.signal_genes
                if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)
            ]
            if not layer_exit:
                errors.append(f"Layer {layer.timeframe}: no exit signal defined")
            if layer.role not in (None, "trend", "execution", "structure", "zone"):
                errors.append(f"Layer {layer.timeframe}: invalid role '{layer.role}'")
            if layer.role in (None, "execution"):
                has_execution = True
            if layer.role == "trend":
                has_trend = True
        if not has_execution:
            errors.append("MTF strategy needs at least one execution layer")

        # Mixed direction requires at least one DIRECTION gene across all layers
        if dna.risk_genes.direction == "mixed":
            all_direction = [
                g for layer in dna.layers
                for g in layer.signal_genes
                if g.role == SignalRole.DIRECTION
            ]
            if not all_direction:
                errors.append(
                    "mixed direction requires at least one DIRECTION gene "
                    "across MTF layers"
                )

        # Validate cross_layer_logic
        if dna.cross_layer_logic not in ("AND", "OR"):
            errors.append(
                f"cross_layer_logic must be 'AND' or 'OR', got '{dna.cross_layer_logic}'"
            )

        # Warn if mixed direction but no trend layer
        if dna.risk_genes.direction == "mixed" and not has_trend:
            warnings.append(
                "mixed direction with layers but no trend layer: "
                "direction signal may not be well-defined"
            )

    # Validate condition structures for top-level genes
    for i, gene in enumerate(dna.signal_genes):
        cond_errors = _validate_condition_structure(gene.condition, i)
        errors.extend(cond_errors)

    # Validate condition structures for layer genes
    if dna.layers:
        for layer in dna.layers:
            for i, gene in enumerate(layer.signal_genes):
                cond_errors = _validate_condition_structure(
                    gene.condition, i, prefix=f"Layer {layer.timeframe} gene {i}"
                )
                errors.extend(cond_errors)

    # Risk genes validation
    risk = dna.risk_genes
    if not (SL_PCT_MIN <= risk.stop_loss <= SL_PCT_MAX):
        errors.append(f"Stop loss {risk.stop_loss} out of range [{SL_PCT_MIN}, {SL_PCT_MAX}]")

    if risk.take_profit is not None and risk.take_profit <= risk.stop_loss:
        errors.append(f"Take profit ({risk.take_profit}) must be greater than stop loss ({risk.stop_loss})")

    if not (0.10 <= risk.position_size <= 1.0):
        errors.append(f"Position size {risk.position_size} out of range [0.10, 1.0]")

    if not (1 <= risk.leverage <= 10):
        errors.append(f"Leverage {risk.leverage} out of range [1, 10]")

    if risk.direction not in ("long", "short", "mixed"):
        errors.append(f"Direction must be 'long', 'short', or 'mixed', got '{risk.direction}'")

    # MTF control parameters validation
    valid_mtf_modes = (None, "direction", "confluence", "direction+confluence")
    if dna.mtf_mode not in valid_mtf_modes:
        errors.append(f"mtf_mode must be one of {valid_mtf_modes}, got '{dna.mtf_mode}'")

    if not (0.1 <= dna.confluence_threshold <= 0.9):
        errors.append(f"confluence_threshold {dna.confluence_threshold} out of range [0.1, 0.9]")

    if not (0.5 <= dna.proximity_mult <= 3.0):
        errors.append(f"proximity_mult {dna.proximity_mult} out of range [0.5, 3.0]")

    return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


def _validate_condition_structure(
    condition: dict, gene_idx: int, prefix: str = "",
) -> List[str]:
    """Validate structure of a condition dict for new condition types."""
    errors: List[str] = []
    label = prefix if prefix else f"Gene {gene_idx}"
    cond_type = condition.get("type", "")

    if cond_type in ("cross_above_series", "cross_below_series"):
        if "target_indicator" not in condition:
            errors.append(f"{label}: {cond_type} requires 'target_indicator'")
    elif cond_type in ("lookback_any", "lookback_all"):
        if "window" not in condition:
            errors.append(f"{label}: {cond_type} requires 'window'")
        if "inner" not in condition:
            errors.append(f"{label}: {cond_type} requires 'inner' condition")
    elif cond_type == "touch_bounce":
        if "direction" not in condition:
            errors.append(f"{label}: touch_bounce requires 'direction'")
    elif cond_type == "role_reversal":
        if "role" not in condition:
            errors.append(f"{label}: role_reversal requires 'role'")
    elif cond_type == "wick_touch":
        if "direction" not in condition:
            errors.append(f"{label}: wick_touch requires 'direction'")

    return errors
