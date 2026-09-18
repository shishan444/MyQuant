"""Tests for OrderGenerator: price calculation and order generation logic."""
import pytest

pytestmark = [pytest.mark.unit]

from core.prediction.predictor import PredictionResult
from core.trading.order_generator import compute_order_price, generate_order
from core.trading.types import (
    AccountState,
    BarSignals,
    JudgmentConfig,
)


# ---------------------------------------------------------------------------
# compute_order_price
# ---------------------------------------------------------------------------


class TestComputeOrderPrice:
    """Test price calculation within predicted range."""

    def setup_method(self):
        self.config = JudgmentConfig(
            pricing_alpha_base=0.3,
            pricing_alpha_range=0.5,
        )

    def test_long_order_within_range(self):
        price, p_fill = compute_order_price(
            prediction_low=94.0,
            prediction_high=98.0,
            prediction_width=2.0,
            direction=1.0,
            confidence=0.5,
            config=self.config,
        )
        # alpha = 0.3 + 0.5*0.5 = 0.55
        # price = 96 - 0.55*2 = 94.9
        assert 94.0 <= price <= 96.0  # within [low, mid]
        assert 0 < p_fill < 1

    def test_short_order_within_range(self):
        price, p_fill = compute_order_price(
            prediction_low=94.0,
            prediction_high=98.0,
            prediction_width=2.0,
            direction=-1.0,
            confidence=0.5,
            config=self.config,
        )
        assert 96.0 <= price <= 98.0  # within [mid, high]
        assert 0 < p_fill < 1

    def test_high_confidence_more_aggressive(self):
        price_high_conf, _ = compute_order_price(
            prediction_low=94.0, prediction_high=98.0,
            prediction_width=2.0, direction=1.0,
            confidence=1.0, config=self.config,
        )
        price_low_conf, _ = compute_order_price(
            prediction_low=94.0, prediction_high=98.0,
            prediction_width=2.0, direction=1.0,
            confidence=0.1, config=self.config,
        )
        # High confidence -> lower price (more aggressive for long)
        assert price_high_conf < price_low_conf

    def test_zero_sigma_returns_mid(self):
        price, p_fill = compute_order_price(
            prediction_low=96.0, prediction_high=96.0,
            prediction_width=0.0, direction=1.0,
            confidence=0.5, config=self.config,
        )
        assert price == 96.0
        assert p_fill == 0.5

    def test_long_price_clamped_to_low(self):
        """Very high alpha should clamp to prediction low."""
        config = JudgmentConfig(pricing_alpha_base=0.8, pricing_alpha_range=0.5)
        price, _ = compute_order_price(
            prediction_low=94.0, prediction_high=98.0,
            prediction_width=2.0, direction=1.0,
            confidence=1.0, config=config,
        )
        assert price >= 94.0  # never below prediction low

    def test_short_price_clamped_to_high(self):
        config = JudgmentConfig(pricing_alpha_base=0.8, pricing_alpha_range=0.5)
        price, _ = compute_order_price(
            prediction_low=94.0, prediction_high=98.0,
            prediction_width=2.0, direction=-1.0,
            confidence=1.0, config=config,
        )
        assert price <= 98.0

    def test_fill_probability_decreases_with_aggression(self):
        _, p_conservative = compute_order_price(
            prediction_low=94.0, prediction_high=98.0,
            prediction_width=2.0, direction=1.0,
            confidence=0.1, config=self.config,
        )
        _, p_aggressive = compute_order_price(
            prediction_low=94.0, prediction_high=98.0,
            prediction_width=2.0, direction=1.0,
            confidence=1.0, config=self.config,
        )
        # Conservative (near mid) has higher fill prob than aggressive (near low)
        assert p_conservative > p_aggressive


# ---------------------------------------------------------------------------
# generate_order
# ---------------------------------------------------------------------------


class TestGenerateOrder:
    """Test order generation from signals + prediction."""

    def _make_state(self, **overrides) -> AccountState:
        defaults = dict(
            balance=10000.0,
            has_position=False,
            position_side="flat",
            position_entry=0.0,
            position_quantity=0.0,
            position_margin=0.0,
            unrealized_pnl=0.0,
            position_bars_held=0,
            target_position_pct=0.30,
            actual_position_pct=0.0,
            equity=10000.0,
        )
        defaults.update(overrides)
        return AccountState(**defaults)

    def test_no_entry_signal_returns_none(self):
        signals = BarSignals(entry=False)
        result = generate_order(signals, None, self._make_state(), JudgmentConfig())
        assert result is None

    def test_entry_with_prediction_produces_limit_order(self):
        signals = BarSignals(entry=True, direction=1.0, confidence=0.5)
        prediction = PredictionResult(low=94.0, high=98.0, width=2.0, k_actual=1.5)
        config = JudgmentConfig(
            use_limit_orders=True,
            pricing_alpha_base=0.2, pricing_alpha_range=0.3,
            pricing_min_fill_prob=0.2,
        )
        result = generate_order(signals, prediction, self._make_state(), config)
        assert result is not None
        assert result.side == "long"
        assert result.order_type == "limit"
        assert result.price > 0
        assert 94.0 <= result.price <= 96.0
        assert result.fill_probability > 0
        assert result.source == "entry"

    def test_entry_without_prediction_falls_back_to_market(self):
        signals = BarSignals(entry=True, direction=1.0)
        result = generate_order(signals, None, self._make_state(), JudgmentConfig())
        assert result is not None
        assert result.order_type == "market"
        assert result.price == 0.0

    def test_direction_filter_blocks_wrong_direction(self):
        signals = BarSignals(entry=True, direction=1.0)
        state = self._make_state(allowed_direction="short")
        result = generate_order(signals, None, state, JudgmentConfig())
        assert result is None

    def test_neutral_direction_returns_none(self):
        signals = BarSignals(entry=True, direction=0.0)
        result = generate_order(signals, None, self._make_state(), JudgmentConfig())
        assert result is None

    def test_confidence_sizing_scales_entry_size(self):
        signals = BarSignals(entry=True, direction=1.0, confidence=0.5)
        config = JudgmentConfig(confidence_sizing_enabled=True, initial_entry_pct=0.33)
        result = generate_order(signals, None, self._make_state(), config)
        assert result is not None
        expected_size = 0.30 * 0.33 * max(0.5, 0.1)
        assert abs(result.size_pct - expected_size) < 0.001

    def test_low_fill_prob_falls_back_to_market(self):
        signals = BarSignals(entry=True, direction=1.0, confidence=0.1)
        # Wide range with aggressive settings -> low fill prob
        prediction = PredictionResult(low=90.0, high=110.0, width=10.0, k_actual=1.5)
        config = JudgmentConfig(
            pricing_alpha_base=0.8, pricing_alpha_range=0.5,
            pricing_min_fill_prob=0.5,
        )
        result = generate_order(signals, prediction, self._make_state(), config)
        assert result is not None
        assert result.order_type == "market"

    def test_add_source_order(self):
        signals = BarSignals(entry=True, direction=1.0)
        result = generate_order(
            signals, None, self._make_state(), JudgmentConfig(), source="add",
        )
        assert result is not None
        assert result.source == "add"

    def test_order_has_unique_id(self):
        signals = BarSignals(entry=True, direction=1.0)
        r1 = generate_order(signals, None, self._make_state(), JudgmentConfig())
        r2 = generate_order(signals, None, self._make_state(), JudgmentConfig())
        assert r1.order_id != r2.order_id
