"""Integration tests for DIRECTION signal gene across the evolution pipeline."""
import random

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import SignalRole, SignalGene, StrategyDNA, RiskGenes, LogicGenes
from core.evolution.population import init_population, create_random_dna
from core.evolution.operators import mutate_add_signal, mutate_risk
from core.strategy.executor import dna_to_signal_set, batch_signal_sets
from core.backtest.engine import BacktestEngine
from core.strategy.validator import validate_dna


def _make_trending_data(n=300):
    """Create synthetic data trending up then down."""
    np.random.seed(55)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")

    half = n // 2
    up = np.linspace(38000, 42000, half)
    down = np.linspace(42000, 38000, n - half)
    close = np.concatenate([up, down])
    close += np.random.randn(n) * 20

    df = pd.DataFrame({
        "open": close * 0.9999, "high": close * 1.002,
        "low": close * 0.998, "close": close, "volume": 1000.0,
    }, index=dates)
    df.index.name = "timestamp"

    rsi = np.full(n, 50.0)
    rsi[30] = 25.0
    rsi[60] = 25.0
    rsi[170] = 25.0
    rsi[200] = 25.0
    rsi[45] = 75.0
    rsi[80] = 75.0
    rsi[185] = 75.0
    rsi[220] = 75.0
    df["rsi_14"] = rsi

    ema = np.full(n, 40000.0)
    ema[:half] = close[:half] * 0.998
    ema[half:] = close[half:] * 1.002
    df["ema_50"] = ema
    df["sma_20"] = ema * 0.999

    return df


class TestEvolutionProducesMixed:
    """Test evolution pipeline preserves mixed direction."""

    def test_population_has_mixed_individuals(self):
        random.seed(42)
        pop = init_population(size=10, direction="mixed")
        mixed = [ind for ind in pop if ind.risk_genes.direction == "mixed"]
        assert len(mixed) > 0, "Population should have mixed individuals"

    def test_mixed_individuals_have_direction_gene(self):
        random.seed(42)
        pop = init_population(size=10, direction="mixed")
        for ind in pop:
            if ind.risk_genes.direction == "mixed":
                has_dir = any(g.role == SignalRole.DIRECTION for g in ind.signal_genes)
                if not has_dir and ind.layers:
                    for layer in ind.layers:
                        has_dir = has_dir or any(
                            g.role == SignalRole.DIRECTION for g in layer.signal_genes
                        )
                assert has_dir, f"Mixed individual should have DIRECTION gene"


class TestMixedStrategyBacktest:
    """Test mixed strategies with DIRECTION gene produce bidirectional trades."""

    def test_mixed_strategy_backtest_both_directions(self):
        """Mixed strategy with DIRECTION gene should produce Long and Short trades."""
        df = _make_trending_data()

        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           None, {"type": "gt", "threshold": 70}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            risk_genes=RiskGenes(
                stop_loss=0.05, take_profit=0.10,
                position_size=0.5, leverage=1, direction="mixed",
            ),
        )

        engine = BacktestEngine(init_cash=100000)
        result = engine.run(dna, df)

        assert result.total_trades > 0, "Should have trades"

        if result.trades_df is not None and len(result.trades_df) > 0:
            directions = result.trades_df["Direction"].unique()
            assert len(directions) >= 2, (
                f"Expected both Long and Short, got: {directions}"
            )

    def test_mixed_differs_from_fixed(self):
        """Mixed strategy should produce different results from long-only."""
        df = _make_trending_data()

        def make_dna(direction):
            return StrategyDNA(
                signal_genes=[
                    SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                               None, {"type": "lt", "threshold": 30}),
                    SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                               None, {"type": "gt", "threshold": 70}),
                    SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                               None, {"type": "price_above"}),
                ],
                logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
                risk_genes=RiskGenes(
                    stop_loss=0.05, take_profit=0.10,
                    position_size=0.5, leverage=1, direction=direction,
                ),
            )

        engine = BacktestEngine(init_cash=100000)
        result_mixed = engine.run(make_dna("mixed"), df)
        result_long = engine.run(make_dna("long"), df)

        different = (
            result_mixed.total_trades != result_long.total_trades
            or abs(result_mixed.total_return - result_long.total_return) > 0.001
        )
        assert different, "Mixed should differ from long-only"


class TestChampionPreservesDirection:
    """Test that evolution champion DNA preserves DIRECTION gene."""

    def test_champion_dna_preserves_direction_gene(self):
        """After creating a population with mixed, individuals preserve DIRECTION gene through mutations."""
        random.seed(42)
        dna = create_random_dna(direction="mixed")
        assert dna.risk_genes.direction == "mixed"

        dir_genes = [g for g in dna.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(dir_genes) >= 1

        # Mutate and check DIRECTION gene persists (mutate_risk shouldn't remove it)
        child = mutate_risk(dna)
        if child.risk_genes.direction == "mixed":
            child_dir = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
            assert len(child_dir) >= 1, "DIRECTION gene should survive mutation"

    def test_direction_gene_survives_add_signal(self):
        """mutate_add_signal should not remove existing DIRECTION gene."""
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("EMA", {"period": 50}, SignalRole.DIRECTION,
                           None, {"type": "price_above"}),
            ],
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="mixed"),
        )

        child = mutate_add_signal(dna)
        dir_genes = [g for g in child.signal_genes if g.role == SignalRole.DIRECTION]
        assert len(dir_genes) >= 1, "DIRECTION gene should survive add_signal"


class TestAPISchemaCompatibility:
    """Test API schema compatibility with DIRECTION gene."""

    def test_api_creates_mixed_evolution_task(self):
        """Verify EvolutionTaskCreate accepts direction='mixed'."""
        from api.schemas import EvolutionTaskCreate

        task = EvolutionTaskCreate(
            symbol="BTCUSDT",
            timeframe="4h",
            direction="mixed",
        )
        assert task.direction == "mixed"

    def test_dna_model_accepts_direction_role(self):
        """Verify DNAModel accepts signal gene with role='direction'."""
        from api.schemas import DNAModel

        dna_data = {
            "strategy_id": "test-123",
            "signal_genes": [
                {
                    "indicator": "EMA",
                    "params": {"period": 50},
                    "role": "direction",
                    "field": None,
                    "condition": {"type": "price_above"},
                },
            ],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "OR",
                            "add_logic": "AND", "reduce_logic": "AND"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": {"stop_loss": 0.05, "take_profit": None,
                           "position_size": 0.3, "leverage": 1, "direction": "mixed"},
        }
        model = DNAModel(**dna_data)
        assert model.signal_genes[0].role == "direction"
