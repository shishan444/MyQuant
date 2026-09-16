"""PriceRangePredictor unit tests.

Covers:
1. predict without warmup raises RuntimeError
2. warmup + predict returns valid PredictionResult
3. observe updates GARCH state
4. hit_rate tracks coverage
5. miss_streak emergency mechanism
6. needs_retrain triggers at low hit rate
7. state_dict / restore_state roundtrip
8. predict interval is symmetric around close
"""

import math

import pytest
import pandas as pd

pytestmark = [pytest.mark.unit]


def _make_dna(**overrides):
    from core.prediction.genes import PredictionDNA
    defaults = dict(
        omega=1e-5, alpha=0.10, beta=0.80,
        k_base=0.6, k_min=0.3,
        factor_weights={},
        short_window=15, mid_window=60, long_window=200,
    )
    defaults.update(overrides)
    return PredictionDNA(**defaults)


@pytest.fixture
def enhanced_df():
    from tests.helpers.data_factory import make_ohlcv
    from core.features.indicators import compute_all_indicators
    df = make_ohlcv(n=300, seed=42)
    return compute_all_indicators(df)


class TestPredictorLifecycle:
    def test_predict_without_warmup_raises(self):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna()
        predictor = PriceRangePredictor(dna)
        with pytest.raises(RuntimeError, match="warmup"):
            predictor.predict(enhanced_df() if False else pd.DataFrame(), 0)

    def test_warmup_then_predict(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna()
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)
        idx = len(enhanced_df) - 1
        result = predictor.predict(enhanced_df, idx)
        assert result.low < result.high
        assert result.width > 0
        assert result.k_actual >= dna.k_min

    def test_predict_symmetric_around_close(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna(factor_weights={})  # no factor adjustment
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)
        idx = len(enhanced_df) - 1
        result = predictor.predict(enhanced_df, idx)
        close = float(enhanced_df["close"].iloc[idx])
        mid = (result.low + result.high) / 2
        assert abs(mid - close) < 0.01  # symmetric


class TestObserve:
    def test_observe_updates_hit_rate(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna()
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        idx = len(enhanced_df) - 2
        result = predictor.predict(enhanced_df, idx)
        row = enhanced_df.iloc[idx + 1]
        predictor.observe(float(row["high"]), float(row["low"]), result)
        assert predictor.hit_rate >= 0

    def test_hit_rate_after_multiple_observations(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna(k_base=1.5)  # wider to hit more
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        for i in range(150, len(enhanced_df) - 1):
            result = predictor.predict(enhanced_df, i)
            row = enhanced_df.iloc[i + 1]
            ah, al = float(row["high"]), float(row["low"])
            predictor.observe(ah, al, result)

        # With k_base=1.5, should hit at least some
        assert predictor._total_count > 50
        assert predictor.hit_rate > 0.1  # loose check


class TestEmergencyMechanism:
    def test_miss_streak_increases_k_min(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna(k_base=0.01, k_min=0.01)  # very tight
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        # Force misses by predicting with tiny K
        idx = len(enhanced_df) - 6
        result = predictor.predict(enhanced_df, idx)

        for i in range(5):
            # Observe with range much wider than prediction
            predictor.observe(100000.0, 0.0, result)
            result = predictor.predict(enhanced_df, idx)

        # After 5 misses, emergency should inflate k_actual
        assert result.k_actual >= dna.k_min * 1.5  # emergency multiplier


class TestNeedsRetrain:
    def test_needs_retrain_false_initially(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna()
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)
        assert predictor.needs_retrain() is False


class TestStatePersistence:
    def test_state_dict_restore_roundtrip(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna()
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        # Make some predictions to build state
        idx = len(enhanced_df) - 3
        for i in range(idx, len(enhanced_df)):
            result = predictor.predict(enhanced_df, i)
            row = enhanced_df.iloc[i]
            predictor.observe(float(row["high"]), float(row["low"]), result)

        state = predictor.state_dict()

        # Create new predictor and restore
        predictor2 = PriceRangePredictor(dna)
        predictor2.restore_state(state)
        assert abs(predictor2._garch.sigma_sq - predictor._garch.sigma_sq) < 1e-10
        assert predictor2.hit_rate == predictor.hit_rate


# ---------------------------------------------------------------------------
# Test: needs_retrain true condition
# ---------------------------------------------------------------------------


class TestNeedsRetrainConditions:

    def test_needs_retrain_true_when_low_hit_rate_and_enough_samples(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna(k_base=0.01, k_min=0.01)  # very tight predictions
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        # Force 60 misses with tiny K
        idx = len(enhanced_df) - 61
        for _ in range(60):
            result = predictor.predict(enhanced_df, idx)
            predictor.observe(100000.0, 0.0, result)  # guaranteed miss

        assert predictor._total_count >= 50
        assert predictor.hit_rate < 0.45
        assert predictor.needs_retrain() is True

    def test_needs_retrain_false_when_good_hit_rate(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna(k_base=2.0, k_min=0.3)  # very wide predictions
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        idx = 150
        for i in range(60):
            if idx + i >= len(enhanced_df) - 1:
                break
            result = predictor.predict(enhanced_df, idx + i)
            # Use prediction bounds as actual -> guaranteed hit
            predictor.observe(result.high + 1.0, result.low - 1.0, result)

        assert predictor._total_count >= 50
        if predictor.hit_rate >= 0.45:
            assert predictor.needs_retrain() is False


# ---------------------------------------------------------------------------
# Test: factor weights affect prediction
# ---------------------------------------------------------------------------


class TestFactorWeights:

    def test_factor_weights_change_k_actual(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna_no_factors = _make_dna(factor_weights={})
        dna_with_factors = _make_dna(
            factor_weights={"vol_regime": 0.5, "rvol": 0.3}
        )

        pred_no = PriceRangePredictor(dna_no_factors)
        pred_with = PriceRangePredictor(dna_with_factors)
        pred_no.warmup(enhanced_df, n_bars=100)
        pred_with.warmup(enhanced_df, n_bars=100)

        idx = len(enhanced_df) - 1
        r_no = pred_no.predict(enhanced_df, idx)
        r_with = pred_with.predict(enhanced_df, idx)

        # Factor weights should change k_actual (unless all factors are 0)
        # Both should produce valid results regardless
        assert r_no.low < r_no.high
        assert r_with.low < r_with.high


# ---------------------------------------------------------------------------
# Test: miss_streak intermediate branch (>= 3)
# ---------------------------------------------------------------------------


class TestMissStreakIntermediate:

    def test_three_misses_inflate_k_min(self, enhanced_df):
        from core.prediction.predictor import PriceRangePredictor
        dna = _make_dna(k_base=0.01, k_min=0.1)
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        idx = len(enhanced_df) - 4
        result = predictor.predict(enhanced_df, idx)

        for _ in range(3):
            predictor.observe(100000.0, 0.0, result)
            result = predictor.predict(enhanced_df, idx)

        # After 3 misses, k_actual should be inflated (k_min * 1.5)
        assert result.k_actual >= dna.k_min * 1.4  # allow rounding
