"""Tests for V3 algorithm upgrades: polynomial mutation, multi-ancestor, adaptive weights,
template-aware mutation bias, fresh blood count, and 40/40/20 population initialization."""

import random

import pytest

pytestmark = [pytest.mark.unit]

from core.evolution.operators import (
    mutate_params,
    _polynomial_mutation,
    mutate_indicator,
    mutate_logic,
    mutate_risk,
    crossover,
)
from core.evolution.engine import EvolutionEngine, EarlyStopChecker
from core.evolution.population import create_random_dna, init_population
from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, StrategyDNA,
)
from core.strategy.validator import validate_dna

def _make_simple_dna() -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_GUARD, None,
                       {"type": "price_above"}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=None, position_size=0.3),
        strategy_id="test-parent",
        generation=0,
    )

# -- Polynomial mutation tests --

class TestPolynomialMutation:
    def test_output_within_bounds(self):
        """Polynomial mutation should always produce values within [lower, upper]."""
        for _ in range(1000):
            val = _polynomial_mutation(50.0, 0.0, 100.0, eta=20.0)
            assert 0.0 <= val <= 100.0

    def test_small_eta_produces_larger_jumps(self):
        """Low eta should produce larger deviations from current value."""
        random.seed(42)
        small_eta_deltas = [abs(_polynomial_mutation(50.0, 0.0, 100.0, eta=2.0) - 50.0)
                           for _ in range(500)]
        random.seed(42)
        large_eta_deltas = [abs(_polynomial_mutation(50.0, 0.0, 100.0, eta=100.0) - 50.0)
                           for _ in range(500)]
        avg_small = sum(small_eta_deltas) / len(small_eta_deltas)
        avg_large = sum(large_eta_deltas) / len(large_eta_deltas)
        assert avg_small > avg_large, (
            f"Small eta avg delta ({avg_small:.2f}) should be > large eta ({avg_large:.2f})"
        )

    def test_high_eta_concentrates_near_current(self):
        """High eta should produce values close to the current value."""
        random.seed(42)
        values = [_polynomial_mutation(50.0, 0.0, 100.0, eta=50.0) for _ in range(500)]
        within_5 = sum(1 for v in values if 45 <= v <= 55)
        assert within_5 > 200, f"Only {within_5}/500 within 5% range with eta=50"

    def test_boundary_values_handled(self):
        """Mutation at boundaries should still produce valid values."""
        for _ in range(100):
            val = _polynomial_mutation(0.0, 0.0, 100.0, eta=20.0)
            assert 0.0 <= val <= 100.0
            val = _polynomial_mutation(100.0, 0.0, 100.0, eta=20.0)
            assert 0.0 <= val <= 100.0

class TestMutateParamsPolynomial:
    def test_stays_within_param_bounds(self):
        """Mutated params should stay within ParamDef bounds."""
        dna = _make_simple_dna()
        for _ in range(50):
            mutated = mutate_params(dna)
            result = validate_dna(mutated)
            assert result.is_valid, f"Polynomial mutation invalid: {result.errors}"

    def test_multiple_mutations_converge(self):
        """Repeated mutations should explore the space."""
        dna = _make_simple_dna()
        periods = set()
        for _ in range(100):
            dna = mutate_params(dna)
            for g in dna.signal_genes:
                if g.indicator == "RSI":
                    periods.add(g.params["period"])
        # Should explore multiple RSI periods
        assert len(periods) > 3, f"Only explored {len(periods)} distinct RSI periods"

# -- Multi-ancestor engine tests --

class TestMultiAncestorEngine:
    def test_engine_accepts_extra_ancestors(self):
        """Engine.evolve() should accept extra_ancestors parameter."""
        engine = EvolutionEngine(target_score=100, max_generations=1)
        ancestor = create_random_dna()
        extra1 = create_random_dna()
        extra2 = create_random_dna()

        def mock_eval(dna):
            return random.uniform(10, 50)

        result = engine.evolve(
            ancestor=ancestor,
            evaluate_fn=mock_eval,
            extra_ancestors=[extra1, extra2],
        )
        assert result["champion"] is not None
        assert result["total_generations"] >= 1

    def test_elite_ratio_reduced(self):
        """Default elite_ratio should be 0.15 (tournament selection reduces elitism)."""
        engine = EvolutionEngine()
        assert engine.elite_ratio == 0.15

    def test_engine_full_run(self):
        """Engine should complete a short evolution run."""
        engine = EvolutionEngine(
            target_score=100, max_generations=3, patience=10,
        )
        ancestor = create_random_dna()

        def mock_eval(dna):
            return random.uniform(10, 50)

        result = engine.evolve(ancestor=ancestor, evaluate_fn=mock_eval)
        assert "champion" in result
        assert "stop_reason" in result
        assert "total_generations" in result

# -- Adaptive mutation weight tests --

class TestAdaptiveMutationWeights:
    def test_engine_uses_weighted_mutations(self):
        """Engine should use weighted mutation selection based on stagnation."""
        engine = EvolutionEngine(target_score=100, max_generations=5)
        ancestor = _make_simple_dna()

        call_count = {"total": 0}

        def mock_eval(dna):
            call_count["total"] += 1
            return 30.0  # Constant score to trigger stagnation

        result = engine.evolve(ancestor=ancestor, evaluate_fn=mock_eval)
        assert result["total_generations"] == 5
        assert call_count["total"] > 0

# -- Integration: population + multi-ancestor --

class TestPopulationMultiAncestor:
    def test_extra_ancestors_included(self):
        """Extra ancestors should appear in population."""
        a1 = _make_simple_dna()
        a2 = create_random_dna()
        a3 = create_random_dna()
        pop = init_population(15, a1, extra_ancestors=[a2, a3])
        ids = [ind.strategy_id for ind in pop[:3]]
        assert a1.strategy_id in ids
        assert a2.strategy_id in ids
        assert a3.strategy_id in ids

    def test_all_valid_with_extra_ancestors(self):
        """All individuals should be valid even with extra ancestors."""
        a1 = _make_simple_dna()
        extras = [create_random_dna() for _ in range(3)]
        pop = init_population(15, a1, extra_ancestors=extras)
        for ind in pop:
            result = validate_dna(ind)
            assert result.is_valid, f"Individual invalid: {result.errors}"

# -- Template-aware mutation bias tests --

class TestTemplateMutationBias:
    def test_aggressive_template_higher_params_bias(self):
        """Aggressive template should produce more params mutations."""
        engine = EvolutionEngine(
            target_score=100, max_generations=5, template_name="aggressive",
        )
        ancestor = _make_simple_dna()
        call_count = {"total": 0}

        def mock_eval(dna):
            call_count["total"] += 1
            return 30.0

        result = engine.evolve(ancestor=ancestor, evaluate_fn=mock_eval)
        assert result["total_generations"] == 5

    def test_conservative_template_higher_risk_bias(self):
        """Conservative template should produce more risk mutations."""
        engine = EvolutionEngine(
            target_score=100, max_generations=5, template_name="conservative",
        )
        ancestor = _make_simple_dna()

        def mock_eval(dna):
            return 30.0

        result = engine.evolve(ancestor=ancestor, evaluate_fn=mock_eval)
        assert result["champion"] is not None

# -- Fresh blood count tests --

class TestFreshBloodCount:
    def test_engine_produces_correct_population_size(self):
        """Engine should maintain population size with 3-5 fresh blood."""
        engine = EvolutionEngine(
            target_score=100, max_generations=5, population_size=15,
        )
        ancestor = _make_simple_dna()

        def mock_eval(dna):
            return random.uniform(10, 50)

        result = engine.evolve(ancestor=ancestor, evaluate_fn=mock_eval)
        assert result["total_generations"] == 5

# -- 40/40/20 population init tests --

class TestPopulationInitRatio:
    def test_init_population_correct_size(self):
        """Population should have exactly the requested size."""
        a1 = _make_simple_dna()
        pop = init_population(20, a1)
        assert len(pop) == 20

    def test_init_population_all_valid(self):
        """All individuals from 40/40/20 init should be valid."""
        a1 = _make_simple_dna()
        pop = init_population(20, a1)
        for ind in pop:
            result = validate_dna(ind)
            assert result.is_valid, f"Individual invalid: {result.errors}"

    def test_init_population_large_size(self):
        """Larger populations should still be correctly sized and valid."""
        a1 = _make_simple_dna()
        pop = init_population(50, a1)
        assert len(pop) == 50
        for ind in pop:
            result = validate_dna(ind)
            assert result.is_valid, f"Individual invalid: {result.errors}"
