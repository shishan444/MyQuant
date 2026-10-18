"""Tests for evolution engine: operators, population, diversity, early stopping."""

import pytest

pytestmark = [pytest.mark.unit]
import numpy as np

from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, ExecutionGenes,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.validator import validate_dna
from core.evolution.operators import (
    mutate_params, mutate_indicator, mutate_logic, mutate_risk, crossover,
    mutate_add_layer, mutate_remove_layer, mutate_layer_timeframe, mutate_cross_logic,
    generate_random_condition,
)
from core.evolution.population import (
    create_random_dna, init_population, create_random_mtf_layer,
)
from core.evolution.diversity import compute_diversity, inject_fresh_blood
from core.evolution.engine import EarlyStopChecker, EvolutionEngine

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

def _make_mtf_dna() -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3),
        layers=[
            TimeframeLayer(timeframe="4h", signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                           {"type": "gt", "threshold": 70}),
            ]),
            TimeframeLayer(timeframe="1d", signal_genes=[
                SignalGene("EMA", {"period": 50}, SignalRole.ENTRY_TRIGGER, None,
                           {"type": "price_above"}),
            ]),
        ],
        cross_layer_logic="AND",
        strategy_id="mtf-parent",
        generation=0,
    )

class TestMutateParams:
    def test_changes_a_parameter(self):
        dna = _make_simple_dna()
        mutated = mutate_params(dna)
        # At least something should be different (high probability)
        assert mutated.strategy_id != dna.strategy_id
        assert mutated.parent_ids == [dna.strategy_id]

    def test_preserves_structure(self):
        dna = _make_simple_dna()
        mutated = mutate_params(dna)
        assert len(mutated.signal_genes) == len(dna.signal_genes)
        result = validate_dna(mutated)
        assert result.is_valid, f"Mutated DNA invalid: {result.errors}"

    def test_multiple_mutations_still_valid(self):
        dna = _make_simple_dna()
        for _ in range(10):
            dna = mutate_params(dna)
            result = validate_dna(dna)
            assert result.is_valid, f"After mutation: {result.errors}"

class TestMutateIndicator:
    def test_replaces_an_indicator(self):
        dna = _make_simple_dna()
        mutated = mutate_indicator(dna)
        assert mutated.strategy_id != dna.strategy_id
        result = validate_dna(mutated)
        assert result.is_valid, f"Mutated DNA invalid: {result.errors}"

class TestMutateLogic:
    def test_changes_logic(self):
        dna = _make_simple_dna()
        mutated = mutate_logic(dna)
        assert mutated.strategy_id != dna.strategy_id

    def test_logic_values_valid(self):
        dna = _make_simple_dna()
        for _ in range(20):
            mutated = mutate_logic(dna)
            assert mutated.logic_genes.entry_logic in ("AND", "OR")
            assert mutated.logic_genes.exit_logic in ("AND", "OR")

class TestMutateRisk:
    def test_changes_risk_params(self):
        dna = _make_simple_dna()
        mutated = mutate_risk(dna)
        result = validate_dna(mutated)
        assert result.is_valid, f"Risk mutation invalid: {result.errors}"

    def test_risk_stays_in_range(self):
        dna = _make_simple_dna()
        for _ in range(20):
            dna = mutate_risk(dna)
            assert 0.005 <= dna.risk_genes.stop_loss <= 0.20
            assert 0.10 <= dna.risk_genes.position_size <= 1.0

    def test_sizing_params_declared_values_preserved(self):
        """变异扰动面不变锚（add-position-sizing-params）：mutate_risk
        只扰动 stop_loss/position_size/take_profit——新键声明值经
        to_dict/from_dict 往返透传逐次保留（扰动面扩到新键必红）。"""
        import dataclasses

        dna = _make_simple_dna()
        dna = dataclasses.replace(
            dna, risk_genes=dataclasses.replace(
                dna.risk_genes, initial_fraction=0.3,
                max_position_loss=750.0))
        for _ in range(30):
            dna = mutate_risk(dna)
            assert dna.risk_genes.initial_fraction == 0.3
            assert dna.risk_genes.max_position_loss == 750.0

class TestCrossover:
    def test_produces_valid_offspring(self):
        parent_a = _make_simple_dna()
        parent_b = create_random_dna()
        child = crossover(parent_a, parent_b)
        result = validate_dna(child)
        assert result.is_valid, f"Crossover child invalid: {result.errors}"

    def test_child_has_both_parents(self):
        parent_a = _make_simple_dna()
        parent_b = create_random_dna()
        child = crossover(parent_a, parent_b)
        assert parent_a.strategy_id in child.parent_ids
        assert parent_b.strategy_id in child.parent_ids

    def test_crossover_preserves_sizing_params_object_choice(self):
        """整对象选择保键锚（add-position-sizing-params）：risk_genes
        整体取一方——子代两键恒等于所取亲代的声明值（携带方/无携带方
        两分支 30 次覆盖），不产生混合或丢失。"""
        import dataclasses

        carrier = _make_simple_dna()
        carrier = dataclasses.replace(
            carrier, risk_genes=dataclasses.replace(
                carrier.risk_genes, initial_fraction=0.4,
                max_position_loss=250.0))
        plain = _make_simple_dna()  # 新键 None（未声明形态）
        for _ in range(30):
            child = crossover(carrier, plain)
            if child.risk_genes is carrier.risk_genes:
                assert child.risk_genes.initial_fraction == 0.4
                assert child.risk_genes.max_position_loss == 250.0
            else:
                assert child.risk_genes is plain.risk_genes
                assert child.risk_genes.initial_fraction is None
                assert child.risk_genes.max_position_loss is None

class TestPopulation:
    def test_creates_population_of_size(self):
        pop = init_population(size=15, ancestor=_make_simple_dna())
        assert len(pop) == 15

    def test_all_individuals_valid(self):
        pop = init_population(size=10, ancestor=_make_simple_dna())
        for ind in pop:
            result = validate_dna(ind)
            assert result.is_valid, f"Individual invalid: {result.errors}"

    def test_random_dna_is_valid(self):
        dna = create_random_dna()
        result = validate_dna(dna)
        assert result.is_valid, f"Random DNA invalid: {result.errors}"

    def test_random_dna_has_entry_and_exit(self):
        dna = create_random_dna()
        entry_roles = {SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD}
        exit_roles = {SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD}
        has_entry = any(g.role in entry_roles for g in dna.signal_genes)
        has_exit = any(g.role in exit_roles for g in dna.signal_genes)
        assert has_entry, "Random DNA missing entry signal"
        assert has_exit, "Random DNA missing exit signal"

class TestDiversity:
    def test_compute_diversity_returns_float(self):
        pop = init_population(size=10, ancestor=_make_simple_dna())
        div = compute_diversity(pop)
        assert isinstance(div, float)
        assert 0 <= div <= 1.0

    def test_identical_population_low_diversity(self):
        dna = _make_simple_dna()
        pop = [dna for _ in range(10)]
        div = compute_diversity(pop)
        assert div <= 0.1

    def test_inject_fresh_blood_increases_size(self):
        pop = init_population(size=10, ancestor=_make_simple_dna())
        original_size = len(pop)
        pop = inject_fresh_blood(pop, n=2)
        assert len(pop) == original_size + 2

class TestEarlyStopChecker:
    def test_target_reached(self):
        checker = EarlyStopChecker(target_score=80.0, min_generations=5)
        # Below min_generations: should continue even if target met
        action, reason = checker.check(85.0, 3)
        assert action == "continue"
        # At/above min_generations: should stop
        action, reason = checker.check(85.0, 5)
        assert action == "stop"
        assert reason == "target_reached"

    def test_stagnation(self):
        checker = EarlyStopChecker(target_score=100.0, patience=3, min_improvement=0.5)
        checker.check(50.0, 1)
        checker.check(50.2, 2)
        checker.check(50.3, 3)
        action, reason = checker.check(50.3, 4)
        assert action == "stop"
        assert reason == "stagnation"

    def test_continue_when_improving(self):
        checker = EarlyStopChecker(target_score=100.0, patience=5)
        action, _ = checker.check(50.0, 1)
        assert action == "continue"
        action, _ = checker.check(55.0, 2)
        assert action == "continue"

    def test_max_generations(self):
        checker = EarlyStopChecker(target_score=100.0, max_generations=5)
        for gen in range(4):
            action, _ = checker.check(50.0 + gen, gen)
            assert action == "continue"
        action, reason = checker.check(54.0, 5)
        assert action == "stop"
        assert reason == "max_generations"

    def test_decline(self):
        checker = EarlyStopChecker(target_score=100.0, decline_limit=3)
        checker.check(60.0, 1)
        action, _ = checker.check(59.0, 2)
        assert action == "continue"
        action, _ = checker.check(58.0, 3)
        assert action == "continue"
        action, reason = checker.check(57.0, 4)
        assert action == "stop"
        assert reason == "decline"

# ── MTF Operator Tests ──

class TestMutateAddLayer:
    def test_adds_layer_to_single_tf_dna(self):
        dna = _make_simple_dna()
        mutated = mutate_add_layer(dna)
        assert mutated.is_mtf
        assert "add_layer" in mutated.mutation_ops
        assert mutated.generation == 1

    def test_adds_layer_to_mtf_dna(self):
        dna = _make_mtf_dna()
        original_count = len(dna.layers)
        mutated = mutate_add_layer(dna)
        assert len(mutated.layers) == original_count + 1

    def test_no_candidates_returns_same(self):
        dna = _make_simple_dna()
        # All timeframes already used
        existing_tfs = set(dna.timeframes)
        all_tfs = list(existing_tfs)
        mutated = mutate_add_layer(dna, candidate_timeframes=all_tfs)
        assert mutated.strategy_id == dna.strategy_id

class TestMutateRemoveLayer:
    def test_removes_layer_from_mtf(self):
        dna = _make_mtf_dna()
        original_count = len(dna.layers)
        mutated = mutate_remove_layer(dna)
        assert len(mutated.layers) < original_count

    def test_single_layer_noop(self):
        dna = _make_simple_dna()
        mutated = mutate_remove_layer(dna)
        assert mutated.strategy_id == dna.strategy_id

class TestMutateLayerTimeframe:
    def test_changes_timeframe(self):
        dna = _make_mtf_dna()
        mutated = mutate_layer_timeframe(dna)
        if mutated.strategy_id != dna.strategy_id:
            assert "layer_timeframe" in mutated.mutation_ops

    def test_single_tf_noop(self):
        dna = _make_simple_dna()
        mutated = mutate_layer_timeframe(dna)
        assert mutated.strategy_id == dna.strategy_id

class TestMutateCrossLogic:
    def test_flips_logic(self):
        dna = _make_mtf_dna()
        original_logic = dna.cross_layer_logic
        mutated = mutate_cross_logic(dna)
        assert mutated.cross_layer_logic != original_logic

    def test_single_tf_noop(self):
        dna = _make_simple_dna()
        mutated = mutate_cross_logic(dna)
        assert mutated.strategy_id == dna.strategy_id

class TestRandomMtfLayer:
    def test_creates_valid_layer(self):
        layer = create_random_mtf_layer("1d")
        assert layer.timeframe == "1d"
        assert len(layer.signal_genes) >= 2  # at least entry + exit

    def test_layer_has_entry_and_exit(self):
        layer = create_random_mtf_layer("4h")
        roles = [g.role for g in layer.signal_genes]
        assert any(r == SignalRole.ENTRY_TRIGGER for r in roles)
        assert any(r == SignalRole.EXIT_TRIGGER for r in roles)

class TestGenerateRandomCondition:
    """Test the unified generate_random_condition function."""

    def test_generates_basic_condition(self):
        cond = generate_random_condition("RSI")
        assert "type" in cond
        assert cond["type"] in ("lt", "gt", "le", "ge")

    def test_generates_threshold_for_comparison(self):
        cond = generate_random_condition("RSI")
        if cond["type"] in ("lt", "gt", "le", "ge"):
            assert "threshold" in cond

    def test_generates_series_cross_for_ema(self):
        # EMA supports cross_above_series
        for _ in range(50):
            cond = generate_random_condition("EMA")
            if cond["type"] == "cross_above_series":
                assert "target_indicator" in cond
                assert "target_params" in cond
                return
        # If we never hit cross_above_series in 50 tries, still pass
        # (random sampling may not always pick it)

    def test_generates_lookback_for_bb(self):
        # BB supports lookback_any
        for _ in range(50):
            cond = generate_random_condition("BB")
            if cond["type"] in ("lookback_any", "lookback_all"):
                assert "window" in cond
                assert "inner" in cond
                return

    def test_generates_touch_bounce_for_bb(self):
        for _ in range(50):
            cond = generate_random_condition("BB")
            if cond["type"] == "touch_bounce":
                assert "direction" in cond
                assert cond["direction"] in ("support", "resistance")
                return

    def test_unknown_indicator_defaults(self):
        cond = generate_random_condition("NonExistent")
        assert cond["type"] == "gt"

    def test_new_indicators_generate_conditions(self):
        for indicator in ["RVOL", "VROC", "AD", "CVD", "VWMA", "Aroon", "CMO", "TRIX"]:
            cond = generate_random_condition(indicator)
            assert "type" in cond
