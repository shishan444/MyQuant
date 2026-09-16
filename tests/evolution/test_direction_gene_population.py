"""Tests for DIRECTION gene in population creation."""
import random

import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import SignalRole, StrategyDNA
from core.evolution.population import (
    _dna_from_template,
    create_random_dna,
    init_population,
    STRATEGY_TEMPLATES,
)
from core.strategy.validator import validate_dna
from core.backtest.engine import BacktestEngine


class TestTemplateDNADirection:
    """Test _dna_from_template preserves mixed direction."""

    def test_mixed_preserved_in_template_dna(self):
        template = STRATEGY_TEMPLATES[0]
        dna = _dna_from_template(template, direction="mixed")
        assert dna.risk_genes.direction == "mixed"

    def test_mixed_dna_has_direction_gene(self):
        template = STRATEGY_TEMPLATES[0]
        dna = _dna_from_template(template, direction="mixed")
        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) >= 1, "Mixed DNA should have at least one DIRECTION gene"

    def test_long_dna_no_direction_gene(self):
        template = STRATEGY_TEMPLATES[0]
        dna = _dna_from_template(template, direction="long")
        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) == 0, "Long DNA should not have DIRECTION gene"

    def test_short_dna_no_direction_gene(self):
        template = STRATEGY_TEMPLATES[0]
        dna = _dna_from_template(template, direction="short")
        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) == 0, "Short DNA should not have DIRECTION gene"


class TestRandomDNADirection:
    """Test create_random_dna preserves mixed direction."""

    def test_mixed_preserved_in_random_dna(self):
        random.seed(42)
        dna = create_random_dna(direction="mixed")
        assert dna.risk_genes.direction == "mixed"

    def test_mixed_dna_has_direction_gene(self):
        random.seed(42)
        dna = create_random_dna(direction="mixed")
        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) >= 1, "Mixed DNA should have DIRECTION gene"

    def test_long_dna_no_direction_gene(self):
        random.seed(42)
        dna = create_random_dna(direction="long")
        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) == 0

    def test_short_dna_no_direction_gene(self):
        random.seed(42)
        dna = create_random_dna(direction="short")
        direction_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(direction_genes) == 0


class TestDirectionGeneValidation:
    """Test DIRECTION gene passes DNA validation."""

    def test_direction_gene_is_valid(self):
        random.seed(42)
        dna = create_random_dna(direction="mixed")
        result = validate_dna(dna)
        assert result.is_valid, f"DIRECTION gene DNA should be valid: {result.errors}"


class TestDirectionGeneDiversity:
    """Test DIRECTION gene uses diverse indicators, not just EMA/SMA."""

    def test_direction_gene_uses_diverse_indicators(self):
        """Over many random DNAs, DIRECTION gene should use more than EMA/SMA."""
        indicators_seen = set()
        for seed in range(200):
            random.seed(seed)
            dna = create_random_dna(direction="mixed")
            dir_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
            if dir_genes:
                indicators_seen.add(dir_genes[0].indicator)

        # Should see at least 3 different indicators
        assert len(indicators_seen) >= 3, (
            f"DIRECTION gene should use diverse indicators, got: {indicators_seen}"
        )

    def test_direction_gene_condition_variety(self):
        """DIRECTION gene should use price_above (trend) and gt (momentum) conditions."""
        conditions_seen = set()
        for seed in range(200):
            random.seed(seed)
            dna = create_random_dna(direction="mixed")
            dir_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
            if dir_genes:
                conditions_seen.add(dir_genes[0].condition.get("type"))

        assert "price_above" in conditions_seen, f"Expected price_above, got: {conditions_seen}"
        assert "gt" in conditions_seen, f"Expected gt (momentum), got: {conditions_seen}"


class TestMixedDNABacktest:
    """Test mixed DNA with DIRECTION gene can run backtest."""

    def test_mixed_dna_backtest_runs(self):
        import numpy as np
        import pandas as pd

        random.seed(42)
        dna = create_random_dna(direction="mixed", timeframe="4h")

        # Create synthetic data with indicator columns
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = 40000 + np.cumsum(np.random.randn(n) * 100)
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close, "volume": 1000.0,
        }, index=dates)
        df.index.name = "timestamp"
        df["rsi_14"] = 50.0
        df["ema_50"] = close.mean()
        df["sma_20"] = close.mean()

        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df)

        assert isinstance(result.total_return, float)
        assert result.total_trades >= 0


class TestPopulationMixed:
    """Test init_population with mixed direction."""

    def test_population_has_mixed_individuals(self):
        random.seed(42)
        pop = init_population(size=10, direction="mixed")

        mixed_count = sum(1 for ind in pop if ind.risk_genes.direction == "mixed")
        assert mixed_count > 0, "At least some individuals should be mixed"

    def test_population_mixed_has_direction_genes(self):
        random.seed(42)
        pop = init_population(size=10, direction="mixed")

        for ind in pop:
            if ind.risk_genes.direction == "mixed":
                direction_genes = [
                    g for g in ind.signal_genes if g.role == SignalRole.DIRECTION
                ]
                # MTF individuals may not have DIRECTION in signal_genes directly
                if ind.layers:
                    for layer in ind.layers:
                        direction_genes += [
                            g for g in layer.signal_genes if g.role == SignalRole.DIRECTION
                        ]
                assert len(direction_genes) >= 1, (
                    f"Mixed individual should have DIRECTION gene. "
                    f"Signal genes: {[g.role for g in ind.signal_genes]}"
                )
