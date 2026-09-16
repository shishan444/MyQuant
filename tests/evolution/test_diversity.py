"""Tests for core.evolution.diversity — genotype/phenotype distance + diversity.

Covers the pure distance functions (genotype_distance, signal_distance,
equity_distance) and compute_diversity. These are the diversity engine's
algorithmic core and were previously untested (diversity.py was at 13.9%).
Property-based assertions (identity=0, symmetry, bounds, fallback) are
preferred over brittle hand-computed scalars.
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, RiskGenes  # noqa: E402
from core.evolution.diversity import (  # noqa: E402
    genotype_distance,
    signal_distance,
    equity_distance,
    compute_diversity,
)


def _risk(leverage=1, direction="long", stop_loss=0.05):
    return RiskGenes(leverage=leverage, direction=direction, stop_loss=stop_loss)


def _gene(indicator="RSI", period=14, role=SignalRole.ENTRY_TRIGGER, cond_type="lt"):
    return SignalGene(
        indicator, {"period": period}, role, None,
        {"type": cond_type, "threshold": 30},
    )


def _dna(indicator="RSI", period=14, cond_type="lt", leverage=1, direction="long",
         stop_loss=0.05):
    return StrategyDNA(
        signal_genes=[_gene(indicator, period, cond_type=cond_type)],
        risk_genes=_risk(leverage, direction, stop_loss),
    )


class TestGenotypeDistance:
    """genotype_distance: structural distance in [0, 1]."""

    def test_identical_dna_is_zero(self):
        assert genotype_distance(_dna(), _dna()) == 0.0

    def test_different_indicator_is_positive(self):
        assert genotype_distance(_dna("RSI"), _dna("EMA")) > 0.0

    def test_different_params_is_positive(self):
        assert genotype_distance(_dna(period=14), _dna(period=50)) > 0.0

    def test_different_condition_type_is_positive(self):
        # condition differs only by type -> +0.5 component
        assert genotype_distance(_dna(cond_type="lt"), _dna(cond_type="gt")) > 0.0

    def test_different_direction_is_positive(self):
        assert genotype_distance(_dna(direction="long"), _dna(direction="short")) > 0.0

    def test_different_leverage_is_positive(self):
        assert genotype_distance(_dna(leverage=1), _dna(leverage=5)) > 0.0

    def test_is_symmetric(self):
        a, b = _dna("RSI", 14), _dna("EMA", 50)
        assert genotype_distance(a, b) == pytest.approx(genotype_distance(b, a))

    def test_is_bounded_in_unit_interval(self):
        a, b = _dna("RSI", 14), _dna("EMA", 200)
        d = genotype_distance(a, b)
        assert 0.0 <= d <= 1.0

    def test_more_different_means_not_smaller(self):
        # same indicator + closer params should be <= different indicator
        close = genotype_distance(_dna("RSI", 14), _dna("RSI", 16))
        far = genotype_distance(_dna("RSI", 14), _dna("EMA", 200))
        assert close <= far

    def test_extra_genes_penalized(self):
        """An individual with an extra gene in the same role is farther than
        one with the same single gene."""
        single = _dna()
        double = StrategyDNA(
            signal_genes=[_gene(), _gene()],
            risk_genes=_risk(),
        )
        assert genotype_distance(single, double) > 0.0


class TestSignalEquityDistance:
    """signal_distance / equity_distance: phenotype distance with diagnostics."""

    def test_signal_distance_falls_back_without_diagnostics(self):
        a, b = _dna(), _dna("EMA")
        # no _eval_diagnostics -> falls back to genotype_distance
        assert signal_distance(a, b) == pytest.approx(genotype_distance(a, b))

    def test_signal_distance_with_diagnostics_uses_metrics(self):
        a = _dna()
        setattr(a, "_eval_diagnostics", {"raw_metrics": {
            "total_trades": 10, "win_rate": 0.5, "annual_return": 0.2, "max_drawdown": 0.1,
        }})
        b = _dna()
        setattr(b, "_eval_diagnostics", {"raw_metrics": {
            "total_trades": 20, "win_rate": 0.3, "annual_return": 0.5, "max_drawdown": 0.2,
        }})
        d = signal_distance(a, b)
        assert 0.0 < d <= 1.0

    def test_signal_distance_bounded(self):
        a = _dna()
        setattr(a, "_eval_diagnostics", {"raw_metrics": {
            "total_trades": 100, "win_rate": 0.9, "annual_return": 1.0, "max_drawdown": 0.5,
        }})
        b = _dna()
        setattr(b, "_eval_diagnostics", {"raw_metrics": {
            "total_trades": 0, "win_rate": 0.0, "annual_return": -1.0, "max_drawdown": 0.0,
        }})
        assert 0.0 <= signal_distance(a, b) <= 1.0

    def test_equity_distance_falls_back_without_dimension_scores(self):
        a, b = _dna(), _dna("EMA")
        # no diagnostics -> equity_distance -> signal_distance -> genotype_distance
        assert equity_distance(a, b) == pytest.approx(genotype_distance(a, b))

    def test_equity_distance_with_dimension_scores(self):
        a = _dna()
        setattr(a, "_eval_diagnostics", {"dimension_scores": {"return": 80.0, "risk": 60.0}})
        b = _dna()
        setattr(b, "_eval_diagnostics", {"dimension_scores": {"return": 40.0, "risk": 60.0}})
        d = equity_distance(a, b)
        assert 0.0 < d <= 1.0


class TestComputeDiversity:
    """compute_diversity: ratio of unique gene signatures in a population."""

    def test_all_unique_is_one(self):
        pop = [_dna("RSI"), _dna("EMA"), _dna("MACD")]
        assert compute_diversity(pop) == 1.0

    def test_all_identical_below_one(self):
        pop = [_dna(), _dna(), _dna()]
        # all share the same signature -> unique=1/3
        assert compute_diversity(pop) == pytest.approx(1 / 3)

    def test_single_individual_is_zero(self):
        assert compute_diversity([_dna()]) == 0.0

    def test_empty_population_is_zero(self):
        assert compute_diversity([]) == 0.0

    def test_partial_uniqueness(self):
        pop = [_dna("RSI"), _dna("RSI"), _dna("EMA")]  # 2 unique / 3
        assert compute_diversity(pop) == pytest.approx(2 / 3)
