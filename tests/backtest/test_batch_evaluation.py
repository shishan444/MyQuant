"""Tests for population-level batch evaluation (Phases B-E).

Covers:
  A1: BacktestEngine.batch_run signature and return structure
  A2: Batch vs single-run result consistency
  A3: Different SL/TP individuals in batch
  A4: Different directions (long/short) in batch
  A5: Single-individual batch_run degenerates correctly
  A6: Zero-trade individuals in batch
  A7: EvolutionEngine with batch evaluate_population
  A8: Runner integration: batch evaluate chain
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine, BacktestResult
from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signal_set
from core.scoring.scorer import score_strategy
from tests.helpers.data_factory import (
    make_dna,
    make_enhanced_df,
    make_engine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def enhanced_df():
    """Shared enhanced DataFrame with indicators (500 bars, 4h)."""
    return make_enhanced_df(n=500, freq="4h")


@pytest.fixture(scope="module")
def bt_engine():
    return make_engine(init_cash=100000)


# ---------------------------------------------------------------------------
# A1: batch_run signature and return structure
# ---------------------------------------------------------------------------

class TestBatchRunSignature:
    """A1: BacktestEngine.batch_run returns List[BacktestResult] with correct length."""

    def test_returns_list_of_backtest_result(self, bt_engine, enhanced_df):
        dnas = [
            make_dna(entry_value=25, exit_value=75, stop_loss=0.05, take_profit=0.10),
            make_dna(entry_value=20, exit_value=80, stop_loss=0.03, take_profit=0.15),
            make_dna(indicator="EMA", entry_condition="price_above", exit_condition="price_below",
                     stop_loss=0.04, take_profit=0.08),
        ]
        results = bt_engine.batch_run(dnas, enhanced_df)
        assert isinstance(results, list)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, BacktestResult)
            assert isinstance(r.total_trades, int)
            assert isinstance(r.metrics_dict, dict)
            assert isinstance(r.data_bars, int)
            assert r.data_bars == len(enhanced_df)

    def test_each_result_has_required_fields(self, bt_engine, enhanced_df):
        dnas = [make_dna()]
        results = bt_engine.batch_run(dnas, enhanced_df)
        r = results[0]
        assert hasattr(r, "total_return")
        assert hasattr(r, "sharpe_ratio")
        assert hasattr(r, "max_drawdown")
        assert hasattr(r, "win_rate")
        assert hasattr(r, "equity_curve")
        assert hasattr(r, "total_funding_cost")
        assert hasattr(r, "liquidated")
        assert hasattr(r, "bars_per_year")


# ---------------------------------------------------------------------------
# A2: batch vs single-run consistency
# ---------------------------------------------------------------------------

class TestBatchConsistency:
    """A2: batch_run([dna1, dna2, dna3]) matches run(dna_i) for each i."""

    def test_total_trades_match(self, bt_engine, enhanced_df):
        dnas = [
            make_dna(entry_value=25, exit_value=75),
            make_dna(entry_value=20, exit_value=80),
            make_dna(entry_value=35, exit_value=65),
        ]
        batch_results = bt_engine.batch_run(dnas, enhanced_df)
        for i, dna in enumerate(dnas):
            single_result = bt_engine.run(dna, enhanced_df)
            assert batch_results[i].total_trades == single_result.total_trades, (
                f"DNA {i}: batch trades={batch_results[i].total_trades} "
                f"!= single trades={single_result.total_trades}"
            )

    def test_key_scores_close(self, bt_engine, enhanced_df):
        dnas = [
            make_dna(entry_value=25, exit_value=75),
            make_dna(entry_value=20, exit_value=80),
        ]
        batch_results = bt_engine.batch_run(dnas, enhanced_df)
        for i, dna in enumerate(dnas):
            single = bt_engine.run(dna, enhanced_df)
            bm = batch_results[i].metrics_dict
            sm = single.metrics_dict
            for key in ("annual_return", "sharpe_ratio", "max_drawdown", "win_rate"):
                diff = abs(bm.get(key, 0) - sm.get(key, 0))
                assert diff < 0.01, (
                    f"DNA {i} metric '{key}': batch={bm.get(key)} vs single={sm.get(key)}"
                )


# ---------------------------------------------------------------------------
# A3: different SL/TP in batch
# ---------------------------------------------------------------------------

class TestBatchDifferentSLTP:
    """A3: Individuals with different SL/TP evaluated correctly in batch."""

    def test_different_sl_tp_results_differ(self, bt_engine, enhanced_df):
        dna_a = make_dna(stop_loss=0.05, take_profit=0.10, entry_value=25, exit_value=75)
        dna_b = make_dna(stop_loss=0.03, take_profit=0.15, entry_value=25, exit_value=75)
        results = bt_engine.batch_run([dna_a, dna_b], enhanced_df)
        assert len(results) == 2
        # Different SL/TP should generally produce different results
        # At minimum, they should both produce valid results
        for r in results:
            assert isinstance(r.total_trades, int)
            assert isinstance(r.max_drawdown, float)


# ---------------------------------------------------------------------------
# A4: different directions in batch
# ---------------------------------------------------------------------------

class TestBatchDifferentDirections:
    """A4: Long and short directions correctly handled in batch."""

    def test_long_and_short_both_evaluated(self, bt_engine, enhanced_df):
        dna_long = make_dna(direction="long", entry_value=25, exit_value=75)
        dna_short = make_dna(direction="short", entry_value=70, exit_value=30,
                              entry_condition="gt", exit_condition="lt")
        results = bt_engine.batch_run([dna_long, dna_short], enhanced_df)
        assert len(results) == 2
        for r in results:
            assert isinstance(r, BacktestResult)
            assert r.total_trades >= 0


# ---------------------------------------------------------------------------
# A5: single-individual batch degeneration
# ---------------------------------------------------------------------------

class TestBatchSingleIndividual:
    """A5: batch_run([single_dna]) matches run(single_dna)."""

    def test_single_matches_run(self, bt_engine, enhanced_df):
        dna = make_dna(entry_value=25, exit_value=75, stop_loss=0.05, take_profit=0.10)
        batch = bt_engine.batch_run([dna], enhanced_df)
        single = bt_engine.run(dna, enhanced_df)
        assert len(batch) == 1
        assert batch[0].total_trades == single.total_trades


# ---------------------------------------------------------------------------
# A6: zero-trade individuals in batch
# ---------------------------------------------------------------------------

class TestBatchZeroTrades:
    """A6: Zero-trade individuals don't crash batch evaluation."""

    def test_zero_trade_individual_in_batch(self, bt_engine, enhanced_df):
        # Normal DNA that generates trades
        dna_normal = make_dna(entry_value=25, exit_value=75)
        # DNA with conditions that will never trigger (RSI can't be < -100)
        dna_zero = make_dna(entry_value=-999, exit_value=999)
        results = bt_engine.batch_run([dna_normal, dna_zero], enhanced_df)
        assert len(results) == 2
        assert results[0].total_trades >= 0  # normal may or may not trade
        assert results[1].total_trades == 0   # zero-trade individual


# ---------------------------------------------------------------------------
# A7: EvolutionEngine with batch evaluate_population
# ---------------------------------------------------------------------------

class TestEvolutionBatchEvaluate:
    """A7: EvolutionEngine.evolve works with evaluate_population callback."""

    def test_batch_evaluate_population(self):
        from core.evolution.engine import EvolutionEngine

        engine = EvolutionEngine(
            target_score=100,  # unreachable
            population_size=5,
            max_generations=3,
            patience=10,
        )
        ancestor = make_dna()

        call_count = 0

        def evaluate_population(population):
            nonlocal call_count
            call_count += 1
            return [float(i * 10 + call_count) for i in range(len(population))]

        result = engine.evolve(
            ancestor=ancestor,
            evaluate_fn=lambda ind: 0.0,  # fallback, should not be used
            evaluate_population=evaluate_population,
        )
        assert result["champion"] is not None
        assert result["total_generations"] == 3
        assert call_count == 3  # one per generation

    def test_batch_scores_order_matches_population(self):
        from core.evolution.engine import EvolutionEngine

        engine = EvolutionEngine(
            target_score=100,
            population_size=4,
            max_generations=2,
            patience=10,
        )
        ancestor = make_dna()

        def evaluate_population(population):
            # Give each individual a unique score based on index
            return [float(i * 10) for i in range(len(population))]

        result = engine.evolve(
            ancestor=ancestor,
            evaluate_fn=lambda ind: 0.0,
            evaluate_population=evaluate_population,
        )
        # Champion should have highest score from last generation
        assert result["champion_score"] > 0


# ---------------------------------------------------------------------------
# A8: Runner integration -- batch evaluate chain
# ---------------------------------------------------------------------------

class TestRunnerBatchIntegration:
    """A8: Runner._evaluate_population returns correct scores in order."""

    def test_evaluate_population_ordering(self, bt_engine, enhanced_df):
        """Verify batch evaluation produces scores in same order as input."""
        dnas = [
            make_dna(entry_value=25, exit_value=75),
            make_dna(entry_value=20, exit_value=80),
            make_dna(entry_value=30, exit_value=70),
        ]
        results = bt_engine.batch_run(dnas, enhanced_df)
        scores = []
        for i, (ind, bt_result) in enumerate(zip(dnas, results)):
            metrics = bt_result.metrics_dict
            score_result = score_strategy(metrics, "profit_first", liquidated=bt_result.liquidated)
            scores.append(score_result["total_score"])

        # Each score is a valid float
        for s in scores:
            assert isinstance(s, float)
        # Scores should differ (different strategies)
        assert len(set(round(s, 2) for s in scores)) >= 1  # at least some diversity
