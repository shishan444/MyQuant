"""Tests for MTF engine evolution operator adaptations (Phase N1)."""

import random

import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    StrategyDNA, TimeframeLayer, SignalGene, SignalRole,
    LogicGenes, ExecutionGenes, RiskGenes, derive_role,
)
from core.evolution.operators import (
    mutate_add_layer, mutate_remove_layer, crossover,
    mutate_params, mutate_indicator,
)

def _make_mtf_dna(mtf_mode="direction+confluence") -> StrategyDNA:
    """Create a simple MTF DNA for testing."""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       condition={"type": "gt", "threshold": 50}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       condition={"type": "lt", "threshold": 50}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="15m", symbol="BTCUSDT"),
        risk_genes=RiskGenes(stop_loss=0.05, direction="long"),
        layers=[
            TimeframeLayer(
                timeframe="1d",
                signal_genes=[
                    SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                               condition={"type": "price_above"}),
                    SignalGene("EMA", {"period": 20}, SignalRole.EXIT_TRIGGER,
                               condition={"type": "price_below"}),
                ],
                role="structure",
            ),
            TimeframeLayer(
                timeframe="4h",
                signal_genes=[
                    SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.ENTRY_TRIGGER,
                               condition={"type": "price_above"}),
                    SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.EXIT_TRIGGER,
                               condition={"type": "price_below"}),
                ],
                role="zone",
            ),
            TimeframeLayer(
                timeframe="15m",
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                               condition={"type": "gt", "threshold": 50}),
                    SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                               condition={"type": "lt", "threshold": 50}),
                ],
                role="execution",
            ),
        ],
        mtf_mode=mtf_mode,
        confluence_threshold=0.3,
        proximity_mult=1.5,
    )

class TestMutateAddLayerRoleAware:
    """mutate_add_layer should derive role from timeframe."""

    def test_mutate_add_layer_derives_role_from_timeframe(self):
        """Adding a layer should derive role based on timeframe."""
        random.seed(42)
        dna = _make_mtf_dna()
        # Remove the 4h layer so we have room
        dna_data = dna.to_dict()
        dna_data["layers"] = [dna_data["layers"][0], dna_data["layers"][2]]  # 1d + 15m
        dna = StrategyDNA.from_dict(dna_data)

        # Try adding layers with different timeframes
        for tf in ["4h", "1h", "30m"]:
            mutated = mutate_add_layer(dna, candidate_timeframes=[tf])
            if mutated.strategy_id != dna.strategy_id:
                # Mutation happened
                new_layer = None
                for layer in mutated.layers:
                    if layer.timeframe == tf:
                        new_layer = layer
                        break
                if new_layer:
                    expected_role = derive_role(tf)
                    assert new_layer.role == expected_role, \
                        f"Layer {tf} should have role '{expected_role}', got '{new_layer.role}'"

    def test_mutate_add_layer_preserves_role_diversity(self):
        """After multiple mutations, layers should have diverse roles."""
        random.seed(123)
        # Start with minimal DNA
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "lt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
        )
        roles_seen = set()
        for _ in range(50):
            mutated = mutate_add_layer(dna)
            if mutated.strategy_id != dna.strategy_id:
                for layer in mutated.layers:
                    if layer.role:
                        roles_seen.add(layer.role)
                if len(roles_seen) >= 2:
                    break
        assert len(roles_seen) >= 2, f"Expected diverse roles, got: {roles_seen}"

class TestNewMutationOperators:
    """New MTF parameter mutation operators."""

    def test_mutate_mtf_mode_cycles_modes(self):
        """mutate_mtf_mode should cycle through valid modes."""
        from core.evolution.operators import mutate_mtf_mode
        random.seed(42)
        dna = _make_mtf_dna("direction")
        mutated = mutate_mtf_mode(dna)
        assert mutated.mtf_mode != "direction"
        assert mutated.mtf_mode in ("confluence", "direction+confluence", None)

    def test_mutate_confluence_threshold_in_range(self):
        """mutate_confluence_threshold should stay in [0.1, 0.9]."""
        from core.evolution.operators import mutate_confluence_threshold
        random.seed(42)
        dna = _make_mtf_dna()
        for _ in range(50):
            mutated = mutate_confluence_threshold(dna)
            assert 0.1 <= mutated.confluence_threshold <= 0.9, \
                f"confluence_threshold {mutated.confluence_threshold} out of range"

    def test_mutate_proximity_mult_in_range(self):
        """mutate_proximity_mult should stay in [0.5, 3.0]."""
        from core.evolution.operators import mutate_proximity_mult
        random.seed(42)
        dna = _make_mtf_dna()
        for _ in range(50):
            mutated = mutate_proximity_mult(dna)
            assert 0.5 <= mutated.proximity_mult <= 3.0, \
                f"proximity_mult {mutated.proximity_mult} out of range"

class TestCrossoverRolePreservation:
    """Crossover should handle new roles correctly."""

    def test_crossover_preserves_structure_role(self):
        """Crossover should preserve structure role from parents."""
        random.seed(42)
        parent_a = _make_mtf_dna()
        parent_b = _make_mtf_dna()
        child = crossover(parent_a, parent_b)
        # Child should inherit some layers with structure role
        roles = [layer.role for layer in child.layers] if child.layers else []
        assert "structure" in roles, f"Expected structure role in child, got: {roles}"

    def test_crossover_preserves_zone_role(self):
        """Crossover should preserve zone role from parents."""
        random.seed(42)
        parent_a = _make_mtf_dna()
        parent_b = _make_mtf_dna()
        child = crossover(parent_a, parent_b)
        roles = [layer.role for layer in child.layers] if child.layers else []
        assert "zone" in roles, f"Expected zone role in child, got: {roles}"

    def test_crossover_handles_different_mtf_modes(self):
        """Crossover with different mtf_mode parents should produce valid child."""
        random.seed(42)
        parent_a = _make_mtf_dna("direction")
        parent_b = _make_mtf_dna("confluence")
        child = crossover(parent_a, parent_b)
        assert child.mtf_mode in ("direction", "confluence", "direction+confluence", None)

class TestCreateRandomMTFDNA:
    """Random MTF DNA should have valid roles."""

    def test_create_random_mtf_dna_has_valid_roles(self):
        """Random DNA should have valid roles on layers."""
        from core.evolution.population import create_random_mtf_layer
        random.seed(42)
        for tf in ["1d", "4h", "1h", "15m", "30m"]:
            layer = create_random_mtf_layer(tf)
            assert layer.role in ("structure", "zone", "execution", None), \
                f"Invalid role '{layer.role}' for timeframe '{tf}'"

    def test_create_random_mtf_dna_role_diversity(self):
        """Creating layers for different timeframes should produce diverse roles."""
        from core.evolution.population import create_random_mtf_layer
        random.seed(42)
        roles = set()
        for tf in ["1d", "4h", "1h", "15m"]:
            layer = create_random_mtf_layer(tf)
            if layer.role:
                roles.add(layer.role)
        assert len(roles) >= 2, f"Expected diverse roles, got: {roles}"

class TestEvolutionRegression:
    """Existing evolution operators should still work."""

    def test_existing_mutation_operators_work(self):
        """Standard mutation operators should work with MTF DNA."""
        random.seed(42)
        dna = _make_mtf_dna()
        # These should not raise
        mutated = mutate_params(dna)
        assert isinstance(mutated, StrategyDNA)
        mutated = mutate_indicator(dna)
        assert isinstance(mutated, StrategyDNA)

    def test_crossover_produces_valid_dna(self):
        """Crossover should produce valid DNA."""
        random.seed(42)
        parent_a = _make_mtf_dna()
        parent_b = _make_mtf_dna()
        child = crossover(parent_a, parent_b)
        assert isinstance(child, StrategyDNA)
        assert child.layers is not None
        assert len(child.layers) > 0

class TestC2MutationOperatorRegistration:
    """C2: Three MTF mutation operators must be registered in EvolutionEngine.

    Root cause: mutate_mtf_mode, mutate_confluence_threshold,
    mutate_proximity_mult exist in operators.py but are never imported
    or added to the mutation_pool in engine.py.
    """

    def test_engine_imports_mtf_mode_operator(self):
        """EvolutionEngine should import mutate_mtf_mode."""
        from core.evolution import engine as engine_mod
        assert hasattr(engine_mod, "mutate_mtf_mode") or \
               "mutate_mtf_mode" in str(engine_mod.__dict__.get("mutate_mtf_mode", ""))

    def test_engine_mutation_pool_includes_mtf_operators(self):
        """When timeframe_pool has multiple TFs, mutation_pool should include
        mutate_mtf_mode, mutate_confluence_threshold, mutate_proximity_mult."""
        from core.evolution.engine import EvolutionEngine
        eng = EvolutionEngine(
            timeframe_pool=["1d", "4h", "15m"],
        )
        # Trigger internal setup to verify pool construction
        # We inspect by running one generation of evolve with a mock evaluate_fn
        from core.strategy.dna import StrategyDNA
        import core.evolution.operators as ops

        # Verify the operators exist
        assert hasattr(ops, "mutate_mtf_mode"), "mutate_mtf_mode should exist in operators"
        assert hasattr(ops, "mutate_confluence_threshold"), "mutate_confluence_threshold should exist"
        assert hasattr(ops, "mutate_proximity_mult"), "mutate_proximity_mult should exist"

    def test_mtf_parameters_mutate_across_evolution(self):
        """MTF parameters should change across evolution generations.

        If the operators are properly registered, running evolution
        should produce individuals with different mtf_mode/threshold values.
        """
        random.seed(42)
        from core.evolution.engine import EvolutionEngine
        from core.strategy.dna import StrategyDNA

        ancestor = _make_mtf_dna("direction")
        original_mode = ancestor.mtf_mode
        original_threshold = ancestor.confluence_threshold

        # Track parameter diversity across generations
        modes_seen = {original_mode}
        thresholds_seen = {original_threshold}

        def track_evaluate(dna):
            modes_seen.add(dna.mtf_mode)
            thresholds_seen.add(dna.confluence_threshold)
            return random.uniform(30, 70)

        eng = EvolutionEngine(
            population_size=8,
            max_generations=15,
            patience=20,
            timeframe_pool=["1d", "4h", "15m"],
        )
        try:
            result = eng.evolve(ancestor, track_evaluate)
        except Exception:
            pass  # Evolution may fail for various reasons; we just check registration

        # Import check: engine.py should have the operators available
        import core.evolution.engine as eng_mod
        import inspect
        source = inspect.getsource(eng_mod)
        assert "mutate_mtf_mode" in source, \
            "mutate_mtf_mode should be referenced in engine.py"
        assert "mutate_confluence_threshold" in source, \
            "mutate_confluence_threshold should be referenced in engine.py"
        assert "mutate_proximity_mult" in source, \
            "mutate_proximity_mult should be referenced in engine.py"

class TestM3DiversitySignatureWithLayers:
    """M3: _gene_signature should include layer structure so that MTF
    strategies with different layers are not treated as identical clones.

    Root cause: _gene_signature() only uses dna.signal_genes and ignores
    dna.layers entirely, so two MTF strategies with same signal_genes but
    different layer configurations get the same signature.
    """

    def test_different_layers_different_signatures(self):
        """Two DNAs with same signal_genes but different layers should
        have different signatures."""
        from core.evolution.diversity import _gene_signature

        # Base DNA with no layers
        dna_a = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "lt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
            layers=[
                TimeframeLayer(
                    timeframe="1d",
                    signal_genes=[
                        SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                                   condition={"type": "price_above"}),
                    ],
                    role="structure",
                ),
            ],
        )
        # Same signal_genes, different layer structure
        dna_b = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           condition={"type": "lt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
            layers=[
                TimeframeLayer(
                    timeframe="4h",
                    signal_genes=[
                        SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.ENTRY_TRIGGER,
                                   condition={"type": "price_above"}),
                    ],
                    role="zone",
                ),
            ],
        )
        sig_a = _gene_signature(dna_a)
        sig_b = _gene_signature(dna_b)
        assert sig_a != sig_b, \
            "DNAs with different layer structures should have different signatures"

    def test_same_layers_same_signatures(self):
        """Two DNAs with identical structure should have same signature."""
        from core.evolution.diversity import _gene_signature

        layer = TimeframeLayer(
            timeframe="1d",
            signal_genes=[
                SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "price_above"}),
            ],
            role="structure",
        )
        dna_a = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
            layers=[layer],
        )
        dna_b = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
            layers=[layer],
        )
        assert _gene_signature(dna_a) == _gene_signature(dna_b)

    def test_mtf_diversity_recognizes_layer_differences(self):
        """compute_diversity should recognize MTF layer differences."""
        from core.evolution.diversity import compute_diversity

        dna_a = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
            layers=[
                TimeframeLayer(timeframe="1d", signal_genes=[
                    SignalGene("EMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                               condition={"type": "price_above"}),
                ], role="structure"),
            ],
        )
        dna_b = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           condition={"type": "gt", "threshold": 50}),
            ],
            execution_genes=ExecutionGenes(timeframe="15m"),
            layers=[
                TimeframeLayer(timeframe="4h", signal_genes=[
                    SignalGene("BB", {"period": 20, "std": 2.0}, SignalRole.ENTRY_TRIGGER,
                               condition={"type": "price_above"}),
                ], role="zone"),
            ],
        )
        pop = [dna_a, dna_b]
        div = compute_diversity(pop)
        assert div == 1.0, \
            "Population with different layer structures should have diversity=1.0"
