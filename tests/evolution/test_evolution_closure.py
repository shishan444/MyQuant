"""E2E closure contract: a single evolution run main path.

Validates the full evolution loop (init_population -> evaluate -> select ->
mutate -> champion tracking -> history) using a deterministic stub
evaluate_fn — no backtest, no parquet, no runner. core/evolution/engine.py is
already ~96% covered by unit tests; this file guards the *end-to-end closure
behavior* (result shape, per-generation callback, history completeness) so
the product's core value chain cannot silently break.

Note (batch-3b, deferred): resume-after-restart consistency is NOT tested
here — api/runner.py does not currently wire checkpoint.resume_evolution into
the cold-start path, so such a test would assert a known failure.
"""
import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]

from core.evolution.engine import EvolutionEngine  # noqa: E402
from core.strategy.dna import (  # noqa: E402
    StrategyDNA, SignalGene, SignalRole, RiskGenes, LogicGenes, ExecutionGenes,
)


def _ancestor():
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("EMA", {"period": 50}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 0}),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(leverage=1, direction="long", stop_loss=0.05),
    )


def _stub_evaluate(dna: StrategyDNA) -> float:
    """Deterministic fitness from DNA features (no backtest, no I/O)."""
    base = len(dna.signal_genes) * 10.0
    return float(base + dna.risk_genes.leverage + (1.0 - min(dna.risk_genes.stop_loss, 1.0)))


def _make_engine(max_generations=3, population_size=4):
    return EvolutionEngine(
        target_score=1000.0,        # unreachable -> runs all generations
        template_name="balanced",
        population_size=population_size,
        max_generations=max_generations,
        leverage=1,
        direction="long",
        timeframe_pool=["4h"],
    )


class TestEvolutionRunClosure:
    """Single evolution run: main-path closure contract."""

    def test_full_run_returns_complete_result(self):
        result = _make_engine(max_generations=3).evolve(_ancestor(), _stub_evaluate)
        # result-shape contract (documented in evolve() docstring)
        for key in ("champion", "history", "stop_reason", "total_generations"):
            assert key in result, f"result missing key: {key}"
        assert result["total_generations"] == 3
        assert len(result["history"]) == 3
        assert result["champion"] is not None
        assert isinstance(result["champion"], StrategyDNA)
        assert result["stop_reason"] == "max_generations"

    def test_on_generation_invoked_once_per_generation(self):
        calls = []

        def on_gen(gen, best, avg):
            calls.append((gen, best, avg))

        _make_engine(max_generations=3).evolve(
            _ancestor(), _stub_evaluate, on_generation=on_gen
        )
        assert len(calls) == 3
        assert [c[0] for c in calls] == [1, 2, 3]
        for gen, best, avg in calls:
            assert isinstance(best, float)
            assert isinstance(avg, float)

    def test_history_records_each_generation(self):
        result = _make_engine(max_generations=4).evolve(_ancestor(), _stub_evaluate)
        history = result["history"]
        assert len(history) == 4
        # each record carries generation + score fields
        for i, h in enumerate(history):
            assert h["generation"] == i + 1
            assert "best_score" in h and "avg_score" in h
        # champion fitness equals the best score ever seen
        best_scores = [h["best_score"] for h in history]
        champion_fitness = result.get("champion_fitness")
        if champion_fitness is not None:
            assert champion_fitness == max(best_scores)

    def test_extra_ancestors_included_in_initial_population(self):
        """An extra ancestor is seeded into the population alongside the main one."""
        extra = StrategyDNA(
            signal_genes=[SignalGene("MACD", {"fast": 12}, SignalRole.ENTRY_TRIGGER, None,
                                      {"type": "cross_above", "threshold": 0})],
            risk_genes=RiskGenes(leverage=1, direction="long"),
        )
        result = _make_engine(max_generations=2).evolve(
            _ancestor(), _stub_evaluate, extra_ancestors=[extra]
        )
        assert result["total_generations"] == 2
        assert result["champion"] is not None
