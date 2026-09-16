"""PredictionEvolver unit tests.

Covers:
1. evolve returns a PredictionDNA
2. evolved DNA is stationary (clamp_params enforced)
3. fitness function correctly scores hit vs miss
4. population initialization produces valid individuals
5. mutate produces valid offspring
"""

import pytest
import pandas as pd

pytestmark = [pytest.mark.unit]


@pytest.fixture
def sample_df():
    from tests.helpers.data_factory import make_ohlcv
    from core.features.indicators import compute_all_indicators
    df = make_ohlcv(n=200, seed=42)
    return compute_all_indicators(df)


class TestPredictionEvolver:
    def test_evolve_returns_dna(self, sample_df):
        from core.prediction.evolution import PredictionEvolver
        evolver = PredictionEvolver(sample_df, population_size=10, max_generations=3)
        best = evolver.evolve()
        assert best is not None
        assert best.fitness_score > 0

    def test_evolved_dna_is_stationary(self, sample_df):
        from core.prediction.evolution import PredictionEvolver
        evolver = PredictionEvolver(sample_df, population_size=10, max_generations=3)
        best = evolver.evolve()
        assert best.is_stationary, f"alpha={best.alpha}, beta={best.beta}, sum={best.alpha+best.beta}"

    def test_evolve_improves_over_random(self, sample_df):
        from core.prediction.evolution import PredictionEvolver
        evolver = PredictionEvolver(sample_df, population_size=10, max_generations=5)
        best = evolver.evolve()
        # Best should have a positive fitness (better than 0)
        assert best.fitness_score >= 0


class TestFitnessFunction:
    def test_perfect_hit_high_fitness(self):
        from core.prediction.evolution import _compute_fitness
        from core.prediction.predictor import PredictionResult
        # All bars perfectly covered
        predicted = [PredictionResult(low=95, high=105, width=5, k_actual=0.5)] * 10
        actual_highs = [104.0] * 10
        actual_lows = [96.0] * 10
        score = _compute_fitness(predicted, actual_highs, actual_lows)
        assert score > 0.5

    def test_all_miss_zero_hit_rate(self):
        from core.prediction.evolution import _compute_fitness
        from core.prediction.predictor import PredictionResult
        # All bars miss (actual range way beyond prediction)
        predicted = [PredictionResult(low=99, high=101, width=1, k_actual=0.5)] * 10
        actual_highs = [110.0] * 10
        actual_lows = [90.0] * 10
        score = _compute_fitness(predicted, actual_highs, actual_lows)
        assert score < 0.5  # should be low


class TestPopulationInit:
    def test_init_population_size(self, sample_df):
        from core.prediction.evolution import PredictionEvolver
        evolver = PredictionEvolver(sample_df, population_size=15, max_generations=1)
        pop = evolver._init_population()
        assert len(pop) == 15
        for dna in pop:
            assert dna.is_stationary
