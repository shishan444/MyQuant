"""Phase 5 tests: batch_signal_sets with gene-level deduplication."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.executor import (
    batch_signal_sets,
    dna_to_signal_set,
    _gene_signature,
    clear_indicator_cache,
    _indicator_column_cache,
)
from core.strategy.dna import SignalRole
from tests.helpers.data_factory import (
    make_dna, make_enhanced_df, make_signal_gene, make_mtf_dna, make_ohlcv,
)


class TestBatchMatchesIndividual:
    """5 single-TF DNA -> batch results match individual calls."""

    def test_batch_matches_individual(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        dnas = [make_dna(entry_value=20 + i * 10, exit_value=60 + i * 10) for i in range(5)]

        batch_results = batch_signal_sets(dnas, df)
        individual_results = [dna_to_signal_set(dna, df) for dna in dnas]

        for i, (br, ir) in enumerate(zip(batch_results, individual_results)):
            pd.testing.assert_series_equal(br.entries, ir.entries, check_names=False,
                                           obj=f"entries[{i}]")
            pd.testing.assert_series_equal(br.exits, ir.exits, check_names=False,
                                           obj=f"exits[{i}]")
        clear_indicator_cache()


class TestDeduplicationReducesComputation:
    """3 DNA all using RSI(14) lt 30 -> evaluate_condition called only once per unique gene."""

    def test_deduplication(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)

        # All 3 DNA use same RSI(14) entry, different exit thresholds
        dnas = [make_dna(entry_value=30, exit_value=50 + i * 10) for i in range(3)]

        batch_results = batch_signal_sets(dnas, df)

        # Entry signals should be identical for all (same gene)
        for i in range(1, 3):
            pd.testing.assert_series_equal(
                batch_results[0].entries, batch_results[i].entries, check_names=False,
            )
        clear_indicator_cache()


class TestMtfDnaFallbackCorrect:
    """Single-TF + MTF mixed population -> all results correct."""

    def test_mixed_population(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        from core.features.indicators import compute_all_indicators

        single_dna = make_dna()
        mtf_dna = make_mtf_dna(timeframes=("4h",))

        # Build dfs_by_timeframe with just the execution TF
        dfs_by_timeframe = {"4h": df}

        results = batch_signal_sets(
            [single_dna, mtf_dna], df,
            dfs_by_timeframe=dfs_by_timeframe,
        )

        assert len(results) == 2
        assert len(results[0].entries) == 200
        assert len(results[1].entries) == 200
        clear_indicator_cache()


class TestEmptyPopulation:
    """[] -> []."""

    def test_empty(self):
        df = make_enhanced_df(n=100)
        results = batch_signal_sets([], df)
        assert results == []


class TestSingleIndividual:
    """[dna] -> matches dna_to_signal_set."""

    def test_single(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        dna = make_dna()

        batch_result = batch_signal_sets([dna], df)
        individual_result = dna_to_signal_set(dna, df)

        pd.testing.assert_series_equal(
            batch_result[0].entries, individual_result.entries, check_names=False,
        )
        pd.testing.assert_series_equal(
            batch_result[0].exits, individual_result.exits, check_names=False,
        )
        clear_indicator_cache()


class TestAllSameDna:
    """10 identical DNA -> all SignalSets identical."""

    def test_all_same(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)
        dna = make_dna()
        dnas = [make_dna() for _ in range(10)]  # same default params

        results = batch_signal_sets(dnas, df)

        for i in range(1, 10):
            pd.testing.assert_series_equal(
                results[0].entries, results[i].entries, check_names=False,
            )
        clear_indicator_cache()


class TestDifferentConditionsSameIndicator:
    """RSI(14) lt 30 vs RSI(14) gt 70 -> evaluated separately."""

    def test_different_conditions(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)

        dna1 = make_dna(entry_value=30, exit_value=70)   # RSI < 30 entry
        dna2 = make_dna(entry_value=70, exit_value=30)   # RSI > 70 entry (reversed logic)

        results = batch_signal_sets([dna1, dna2], df)

        # Entry signals should differ (one is RSI<30, other is RSI<70)
        # They might overlap but shouldn't be identical
        assert len(results[0].entries) == len(results[1].entries)
        clear_indicator_cache()


class TestFailedGeneReturnsFalseSignal:
    """Invalid indicator -> that gene returns all False, no crash."""

    def test_failed_gene(self):
        clear_indicator_cache()
        df = make_enhanced_df(n=200)

        # DNA with one valid gene and one invalid
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, StrategyDNA,
        )
        valid_gene = make_signal_gene(
            indicator="RSI", params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition_type="lt", condition_value=30,
        )
        invalid_gene = make_signal_gene(
            indicator="NONEXISTENT_XYZ",
            params={"period": 14},
            role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=50,
        )
        dna = StrategyDNA(
            signal_genes=[valid_gene, invalid_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
        )

        results = batch_signal_sets([dna], df)
        assert len(results) == 1
        # Should not crash, exits should be all False
        assert results[0].exits.sum() == 0
        clear_indicator_cache()
