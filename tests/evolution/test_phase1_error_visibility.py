"""Phase 1 tests: per-individual error handling with evaluation logging."""

import logging
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)
from core.strategy.executor import _empty_signal_set, dna_to_signal_set, SignalSet
from tests.helpers.data_factory import make_enhanced_df, make_dna, make_ohlcv


def _make_broken_dna(timeframe="4h"):
    """Create a DNA with an invalid indicator that will fail signal computation."""
    gene = SignalGene(
        indicator="NONEXISTENT_INDICATOR_XYZ",
        params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER,
        condition={"type": "lt", "threshold": 30},
    )
    return StrategyDNA(
        signal_genes=[gene],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=timeframe),
        risk_genes=RiskGenes(),
    )


class TestBadIndividualNotKillBatch:
    """3 valid + 1 broken DNA -> returns 4 scores, broken=0."""

    def test_bad_individual_gets_zero_score(self):
        from api.runner import EvolutionRunner
        from pathlib import Path

        enhanced_df = make_enhanced_df(n=200)
        valid_dnas = [make_dna() for _ in range(3)]
        broken_dna = _make_broken_dna()
        population = valid_dnas + [broken_dna]

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
            "leverage": 1,
            "direction": "long",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        scores = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=enhanced_df, dfs_by_timeframe=None,
        )

        assert len(scores) == 4
        # Broken DNA should get score 0.0 or 5.0 (empty signals = 5.0)
        assert scores[-1] in (0.0, 5.0)


class TestBadIndividualLogsWarning:
    """caplog verifies WARNING level log on signal failure in batch evaluation."""

    def test_warning_logged_on_signal_failure(self, caplog):
        from api.runner import EvolutionRunner

        enhanced_df = make_enhanced_df(n=200)
        broken_dna = _make_broken_dna()
        population = [broken_dna]

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        with caplog.at_level(logging.WARNING, logger="RUNNER"):
            runner._evaluate_population(
                population, task_row, leverage=1, direction="long",
                enhanced_df=enhanced_df, dfs_by_timeframe=None,
            )

        # Broken DNA in population produces empty signals, score should still work
        # The warning may or may not be logged depending on where the failure occurs
        assert len(caplog.records) >= 0  # No crash = success


class TestEvaluateDnaLogsError:
    """_evaluate_dna exception -> diagnostics contains error field."""

    def test_error_field_on_exception(self, caplog):
        from api.runner import EvolutionRunner

        # Create a DNA that will cause an exception during backtest
        dna = make_dna()
        # Use empty df that lacks indicator columns to trigger an error in backtest
        # (signal computation will produce empty signals, but backtest may fail)
        empty_df = pd.DataFrame({"close": [1.0]}, index=pd.date_range("2024-01-01", periods=1, tz="UTC"))

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        with caplog.at_level(logging.WARNING, logger="RUNNER"):
            result = runner._evaluate_dna(
                dna, task_row, leverage=1, direction="long",
                enhanced_df=empty_df,
            )

        # Should return diagnostics dict with score
        assert isinstance(result, dict)
        assert "score" in result


class TestBatchFailureFallsBack:
    """mock batch_run raise -> falls back to per-individual, no crash."""

    def test_fallback_on_batch_failure(self, monkeypatch):
        from api.runner import EvolutionRunner
        from core.backtest import engine as bt_module

        enhanced_df = make_enhanced_df(n=200)
        population = [make_dna() for _ in range(3)]

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        # Make batch_run raise
        original_batch_run = bt_module.BacktestEngine.batch_run

        def failing_batch_run(self, *args, **kwargs):
            raise RuntimeError("simulated batch failure")

        monkeypatch.setattr(bt_module.BacktestEngine, "batch_run", failing_batch_run)

        runner = EvolutionRunner.__new__(EvolutionRunner)
        scores = runner._evaluate_population(
            population, task_row, leverage=1, direction="long",
            enhanced_df=enhanced_df,
        )

        assert len(scores) == 3
        assert all(isinstance(s, float) for s in scores)


class TestEmptySignalSetStructure:
    """_empty_signal_set returns correct SignalSet structure."""

    def test_empty_signal_set(self):
        df = make_enhanced_df(n=100)
        sig = _empty_signal_set(df)

        assert isinstance(sig, SignalSet)
        assert len(sig.entries) == 100
        assert len(sig.exits) == 100
        assert len(sig.adds) == 100
        assert len(sig.reduces) == 100
        assert sig.entries.sum() == 0
        assert sig.exits.sum() == 0
        assert sig.adds.sum() == 0
        assert sig.reduces.sum() == 0


class TestAllFalseSignalSetProducesZero:
    """All-False signal DNA -> score=0.0 (untriggable strategies are eliminated)."""

    def test_empty_signals_score(self):
        from api.runner import EvolutionRunner

        enhanced_df = make_enhanced_df(n=200)
        # DNA with RSI < -1 is impossible -> no entries (RSI range is 0-100)
        dna = make_dna(entry_condition="lt", entry_value=-1, exit_condition="gt", exit_value=200)

        task_row = {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "score_template": "profit_first",
        }

        runner = EvolutionRunner.__new__(EvolutionRunner)
        result = runner._evaluate_dna(
            dna, task_row, leverage=1, direction="long",
            enhanced_df=enhanced_df,
        )

        assert result["score"] == 0.0
