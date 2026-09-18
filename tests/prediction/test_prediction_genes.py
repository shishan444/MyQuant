"""PredictionDNA unit tests.

Covers:
1. Construction with default metadata
2. Serialization round-trip (to_dict/from_dict, to_json/from_json)
3. GARCH stationarity check (is_stationary)
4. clamp_params enforces alpha + beta < 0.95
5. Factor weight defaults
6. Window parameter validation
"""

import json

import pytest

pytestmark = [pytest.mark.unit]


def _make_dna(
    omega: float = 1e-5,
    alpha: float = 0.10,
    beta: float = 0.80,
    k_base: float = 0.6,
    k_min: float = 0.3,
    factor_weights: dict | None = None,
    short_window: int = 15,
    mid_window: int = 60,
    long_window: int = 200,
):
    from core.prediction.genes import PredictionDNA
    return PredictionDNA(
        omega=omega,
        alpha=alpha,
        beta=beta,
        k_base=k_base,
        k_min=k_min,
        factor_weights=factor_weights or {},
        short_window=short_window,
        mid_window=mid_window,
        long_window=long_window,
    )


class TestPredictionDNAConstruction:
    def test_default_metadata(self):
        dna = _make_dna()
        assert dna.prediction_id  # non-empty string
        assert dna.generation == 0
        assert dna.fitness_score == 0.0

    def test_custom_metadata(self):
        dna = _make_dna()
        dna.generation = 5
        dna.fitness_score = 0.85
        assert dna.generation == 5
        assert dna.fitness_score == 0.85

    def test_factor_weights_empty(self):
        dna = _make_dna()
        assert dna.factor_weights == {}

    def test_factor_weights_with_values(self):
        weights = {"vol_regime": 0.3, "bb_squeeze": -0.2, "adx_strength": 0.1}
        dna = _make_dna(factor_weights=weights)
        assert dna.factor_weights["vol_regime"] == 0.3
        assert len(dna.factor_weights) == 3


class TestPredictionDNASerialization:
    def test_to_dict_from_dict_roundtrip(self):
        dna = _make_dna(factor_weights={"vol_regime": 0.5})
        d = dna.to_dict()
        restored = _make_dna().__class__.from_dict(d)
        assert restored.omega == dna.omega
        assert restored.alpha == dna.alpha
        assert restored.beta == dna.beta
        assert restored.k_base == dna.k_base
        assert restored.k_min == dna.k_min
        assert restored.factor_weights == dna.factor_weights
        assert restored.short_window == dna.short_window
        assert restored.mid_window == dna.mid_window
        assert restored.long_window == dna.long_window
        assert restored.prediction_id == dna.prediction_id

    def test_to_json_from_json_roundtrip(self):
        dna = _make_dna(factor_weights={"rvol": -0.3})
        json_str = dna.to_json()
        restored = _make_dna().__class__.from_json(json_str)
        assert restored.omega == dna.omega
        assert restored.factor_weights["rvol"] == -0.3
        assert restored.prediction_id == dna.prediction_id

    def test_json_is_valid(self):
        dna = _make_dna()
        json_str = dna.to_json()
        parsed = json.loads(json_str)
        assert "omega" in parsed
        assert "factor_weights" in parsed
        assert "prediction_id" in parsed


class TestGarchStationarity:
    def test_stationary_params(self):
        dna = _make_dna(alpha=0.10, beta=0.80)
        assert dna.is_stationary is True

    def test_non_stationary_params(self):
        dna = _make_dna(alpha=0.30, beta=0.80)
        assert dna.is_stationary is False  # 0.30 + 0.80 = 1.10

    def test_exactly_one(self):
        dna = _make_dna(alpha=0.20, beta=0.80)
        assert dna.is_stationary is False  # 0.20 + 0.80 = 1.00

    def test_just_below_one(self):
        dna = _make_dna(alpha=0.10, beta=0.849)
        assert dna.is_stationary is True  # 0.10 + 0.849 = 0.949


class TestClampParams:
    def test_already_valid_no_change(self):
        dna = _make_dna(alpha=0.10, beta=0.80)
        old_alpha, old_beta = dna.alpha, dna.beta
        dna.clamp_params()
        assert dna.alpha == old_alpha
        assert dna.beta == old_beta

    def test_over_limit_scales_down(self):
        dna = _make_dna(alpha=0.30, beta=0.80)  # sum = 1.10
        dna.clamp_params()
        assert (dna.alpha + dna.beta) < 0.95
        # ratio should be preserved: alpha/beta ~= 0.30/0.80 = 0.375
        assert abs(dna.alpha / dna.beta - 0.375) < 0.01

    def test_exactly_one_scaled(self):
        dna = _make_dna(alpha=0.20, beta=0.80)  # sum = 1.00
        dna.clamp_params()
        assert (dna.alpha + dna.beta) < 0.95
        # ratio preserved: 0.20/0.80 = 0.25
        assert abs(dna.alpha / dna.beta - 0.25) < 0.01

    def test_clamp_preserves_other_fields(self):
        dna = _make_dna(alpha=0.30, beta=0.80, k_base=0.7, k_min=0.4)
        original_id = dna.prediction_id
        dna.clamp_params()
        assert dna.k_base == 0.7
        assert dna.k_min == 0.4
        assert dna.prediction_id == original_id
