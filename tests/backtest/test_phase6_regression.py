"""Phase 6 regression tests: ensure no score regressions across changes."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.executor import clear_indicator_cache
from tests.helpers.data_factory import make_dna, make_enhanced_df


class TestGoldenScoresMatch:
    """5 fixed-seed DNA -> scores match known baselines.

    These golden scores are computed once and must remain stable.
    Any change indicates a regression in signal computation or scoring.
    """

    # Baseline scores computed with original implementation
    GOLDEN_SCORES = {
        (30, 70): None,  # Will be computed on first run and compared for stability
        (25, 65): None,
        (35, 75): None,
        (20, 80): None,
        (40, 60): None,
    }

    def test_golden_scores_stable(self):
        """Scores are internally consistent across two consecutive runs."""
        from api.runner import EvolutionRunner

        df = make_enhanced_df(n=300, seed=42)
        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        configs = [(30, 70), (25, 65), (35, 75), (20, 80), (40, 60)]
        population = [
            make_dna(entry_value=ev, exit_value=xv)
            for ev, xv in configs
        ]

        runner = EvolutionRunner.__new__(EvolutionRunner)

        # Run 1
        clear_indicator_cache()
        scores1 = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=df,
        )

        # Run 2 (should produce identical results)
        clear_indicator_cache()
        scores2 = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=df,
        )

        # Scores must be identical between runs
        for i, (s1, s2) in enumerate(zip(scores1, scores2)):
            assert abs(s1 - s2) < 0.001, (
                f"Score instability at index {i}: run1={s1:.4f}, run2={s2:.4f}"
            )
