"""Tests for DIRECTION gene in mutation operators."""
import random

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import SignalRole, SignalGene, StrategyDNA, RiskGenes, LogicGenes
from core.evolution.operators import (
    mutate_add_signal,
    mutate_params,
    mutate_indicator,
    mutate_risk,
    crossover,
)


def _make_mixed_dna_without_direction():
    """Create a mixed DNA without DIRECTION gene."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       None, {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       None, {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(direction="mixed"),
    )


def _make_mixed_dna_with_direction():
    """Create a mixed DNA with DIRECTION gene."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       None, {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       None, {"type": "gt", "threshold": 70}),
            SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                       None, {"type": "price_above"}),
        ],
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(direction="mixed"),
    )


class TestMutateAddSignalDirection:
    """Test mutate_add_signal can add DIRECTION gene."""

    def test_can_add_direction_to_mixed(self):
        """mutate_add_signal should be able to add DIRECTION gene to mixed DNA."""
        random.seed(42)
        # Run multiple times to hit the 0.2 probability
        added = False
        for _ in range(100):
            dna = _make_mixed_dna_without_direction()
            child = mutate_add_signal(dna)
            direction_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
            if direction_genes:
                added = True
                break

        assert added, "mutate_add_signal should eventually add DIRECTION gene to mixed DNA"

    def test_no_duplicate_direction(self):
        """Should not add a second DIRECTION gene."""
        dna = _make_mixed_dna_with_direction()

        for _ in range(50):
            child = mutate_add_signal(dna)
            direction_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
            assert len(direction_genes) <= 1, "Should not have duplicate DIRECTION genes"

    def test_no_direction_for_long(self):
        """Long DNA should not get DIRECTION gene from mutate_add_signal."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="long"),
        )

        for _ in range(50):
            child = mutate_add_signal(dna)
            direction_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
            assert len(direction_genes) == 0, "Long DNA should not get DIRECTION gene"


class TestMutateParamsDirection:
    """Test mutate_params can modify DIRECTION gene parameters."""

    def test_mutate_params_can_modify_direction_gene(self):
        """mutate_params should be able to change DIRECTION gene params."""
        dna = _make_mixed_dna_with_direction()

        changed = False
        for _ in range(100):
            child = mutate_params(dna)
            dir_gene = next((g for g in child.signal_genes if g.role == SignalRole.DIRECTION), None)
            if dir_gene and dir_gene.params.get("period") != 50:
                changed = True
                break

        assert changed, "mutate_params should be able to modify DIRECTION gene period"


class TestMutateIndicatorDirection:
    """Test mutate_indicator can change DIRECTION gene indicator."""

    def test_mutate_indicator_can_change_direction_indicator(self):
        """mutate_indicator should be able to replace DIRECTION gene indicator."""
        dna = _make_mixed_dna_with_direction()

        changed = False
        for _ in range(100):
            child = mutate_indicator(dna)
            dir_gene = next((g for g in child.signal_genes if g.role == SignalRole.DIRECTION), None)
            if dir_gene and dir_gene.indicator != "EMA":
                changed = True
                break

        assert changed, "mutate_indicator should be able to change DIRECTION gene indicator"


class TestMutateRiskDirection:
    """Test mutate_risk can cycle direction to mixed."""

    def test_mutate_risk_can_cycle_to_mixed(self):
        """mutate_risk should be able to change direction from long to mixed."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="long"),
        )

        reached_mixed = False
        for _ in range(200):
            child = mutate_risk(dna)
            if child.risk_genes.direction == "mixed":
                reached_mixed = True
                break

        assert reached_mixed, "mutate_risk should be able to change direction to mixed"


class TestCrossoverDirection:
    """Test crossover preserves DIRECTION gene."""

    def test_crossover_inherits_direction_from_parent_a(self):
        """Crossover should inherit DIRECTION gene from parent A."""
        parent_a = _make_mixed_dna_with_direction()
        parent_b = _make_mixed_dna_without_direction()

        random.seed(42)
        child = crossover(parent_a, parent_b)
        direction_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
        # Should inherit from parent A (since parent B has none)
        assert len(direction_genes) == 1, "Should inherit DIRECTION gene from parent A"
        assert direction_genes[0].indicator == "EMA"

    def test_crossover_inherits_direction_from_parent_b(self):
        """Crossover should inherit DIRECTION gene from parent B."""
        parent_a = _make_mixed_dna_without_direction()
        parent_b = _make_mixed_dna_with_direction()

        # Run multiple times to hit random choice of parent B's direction
        inherited = False
        for i in range(50):
            random.seed(i)
            child = crossover(parent_a, parent_b)
            direction_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
            if direction_genes and direction_genes[0].indicator == "EMA":
                inherited = True
                break

        assert inherited, "Should eventually inherit DIRECTION gene from parent B"

    def test_crossover_no_duplicate_direction(self):
        """Crossover should not produce duplicate DIRECTION genes."""
        parent_a = _make_mixed_dna_with_direction()
        parent_b = _make_mixed_dna_with_direction()

        for i in range(50):
            random.seed(i)
            child = crossover(parent_a, parent_b)
            direction_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
            assert len(direction_genes) <= 1, "Should not have duplicate DIRECTION genes"

    def test_crossover_preserves_entry_exit_and_direction(self):
        """Crossover child should have entry, exit, and direction genes."""
        parent_a = _make_mixed_dna_with_direction()
        parent_b = _make_mixed_dna_with_direction()

        random.seed(42)
        child = crossover(parent_a, parent_b)

        roles = {g.role for g in child.signal_genes}
        assert SignalRole.ENTRY_TRIGGER in roles
        assert SignalRole.EXIT_TRIGGER in roles
        assert SignalRole.DIRECTION in roles
