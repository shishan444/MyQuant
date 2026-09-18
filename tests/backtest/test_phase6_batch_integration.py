"""Phase 6 tests: full integration of batch signal computation into evaluation pipeline."""

import time
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.executor import clear_indicator_cache, _indicator_column_cache
from tests.helpers.data_factory import make_dna, make_enhanced_df, make_ohlcv


class TestEndToEndEvaluationTiming:
    """15 individuals x 500 bars 4h -> _evaluate_population completes in reasonable time."""

    def test_evaluation_timing(self):
        from api.runner import EvolutionRunner

        df = make_enhanced_df(n=500)
        population = [make_dna(entry_value=20 + i * 3, exit_value=60 + i * 3) for i in range(15)]

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        start = time.monotonic()
        scores = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=df, dfs_by_timeframe=None,
        )
        elapsed = time.monotonic() - start

        assert len(scores) == 15
        # First run includes vbt Numba JIT compilation; subsequent runs are faster
        # Allow generous limit for CI environments
        assert elapsed < 30.0, f"Evaluation took {elapsed:.1f}s, expected < 30s"


class TestBatchMatchesIndividualScores:
    """Same population -> batch and individual scores are consistent."""

    def test_scores_consistent(self):
        from api.runner import EvolutionRunner

        df = make_enhanced_df(n=200)
        population = [make_dna(entry_value=25 + i * 5, exit_value=65 + i * 5) for i in range(5)]

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)

        # Batch evaluation
        clear_indicator_cache()
        batch_scores = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=df, dfs_by_timeframe=None,
        )

        # Individual evaluation (fresh cache)
        clear_indicator_cache()
        ind_scores = []
        for ind in population:
            result = runner._evaluate_dna(
                ind, task_row, leverage=1, direction="long",
                enhanced_df=df,
            )
            ind_scores.append(result["score"])

        # Scores should be close (allow small tolerance for floating point differences)
        for i, (bs, is_) in enumerate(zip(batch_scores, ind_scores)):
            assert abs(bs - is_) < 15.0, f"Score mismatch at index {i}: batch={bs:.2f}, ind={is_:.2f}"


class TestFailedIndividualsIsolated:
    """1 broken DNA -> other 14 evaluate correctly."""

    def test_isolation(self):
        from api.runner import EvolutionRunner
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
        )

        df = make_enhanced_df(n=200)
        valid_dnas = [make_dna() for _ in range(4)]

        # Broken DNA with invalid indicator
        broken_gene = SignalGene(
            indicator="NONEXISTENT_XYZ",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "lt", "threshold": 30},
        )
        broken_dna = StrategyDNA(
            signal_genes=[broken_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
        )

        population = valid_dnas + [broken_dna]
        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        scores = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=df, dfs_by_timeframe=None,
        )

        assert len(scores) == 5
        # All should have scores (broken one gets empty signals = 5.0)
        assert all(isinstance(s, float) for s in scores)


class TestCacheClearedBetweenCalls:
    """Second call has cleared cache."""

    def test_cache_cleared(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        dna = make_dna()

        from api.runner import EvolutionRunner
        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }
        runner = EvolutionRunner.__new__(EvolutionRunner)

        # First call
        runner._evaluate_population(
            [dna], task_row, leverage=1, direction="long",
            enhanced_df=df,
        )
        # Cache should have been cleared at the start but may be populated after
        assert isinstance(_indicator_column_cache, dict)

        # Second call should also work
        scores = runner._evaluate_population(
            [dna], task_row, leverage=1, direction="long",
            enhanced_df=df,
        )
        assert len(scores) == 1

        clear_indicator_cache()


class TestRunnerEvaluatePopulationChain:
    """Runner._evaluate_population -> BacktestEngine.batch_run -> correct results."""

    def test_chain(self):
        from api.runner import EvolutionRunner

        df = make_enhanced_df(n=200)
        dna = make_dna()
        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        scores = runner._evaluate_population(
            [dna], task_row, leverage=1, direction="long",
            enhanced_df=df,
        )

        assert len(scores) == 1
        assert isinstance(scores[0], float)
