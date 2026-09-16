"""Tests for OrderManager: fill, validity, timeout, and expiry."""
import pytest

pytestmark = [pytest.mark.unit]

from core.trading.order_manager import OrderManager
from core.trading.types import BarSignals, JudgmentConfig, Order


def _make_order(side="long", price=95.0, source="entry", order_id="test-001",
                order_type="limit", predicted_range=(94.0, 98.0), **kwargs) -> Order:
    return Order(
        order_id=order_id,
        created_at_bar=0,
        side=side,
        price=price,
        size_pct=0.1,
        source=source,
        order_type=order_type,
        predicted_range=predicted_range,
        **kwargs,
    )


class TestFillCheck:
    def test_filled_when_bar_touches_price(self):
        mgr = OrderManager(JudgmentConfig())
        order = _make_order(price=95.0)
        mgr.add_order(order)

        result = mgr.manage(bar_high=96.0, bar_low=94.0)
        assert len(result.filled) == 1
        assert result.filled[0].price == 95.0
        assert mgr.pending_count == 0

    def test_not_filled_when_bar_misses_price(self):
        mgr = OrderManager(JudgmentConfig())
        order = _make_order(price=93.0)
        mgr.add_order(order)

        result = mgr.manage(bar_high=96.0, bar_low=94.0)
        assert len(result.filled) == 0
        assert mgr.pending_count == 1

    def test_market_order_fills_immediately(self):
        mgr = OrderManager(JudgmentConfig())
        order = _make_order(price=0.0, order_type="market")
        mgr.add_order(order)

        result = mgr.manage(bar_high=96.0, bar_low=94.0)
        assert len(result.filled) == 1
        assert result.filled[0].reason == "market"

    def test_fill_at_exact_boundary(self):
        mgr = OrderManager(JudgmentConfig())
        order = _make_order(price=94.0)
        mgr.add_order(order)

        result = mgr.manage(bar_high=96.0, bar_low=94.0)
        assert len(result.filled) == 1


class TestTimeoutCheck:
    def test_order_expires_after_max_wait(self):
        mgr = OrderManager(JudgmentConfig(order_max_wait_bars=3))
        order = _make_order(price=90.0)  # won't be touched
        mgr.add_order(order)

        for _ in range(2):
            mgr.manage(bar_high=96.0, bar_low=94.0)
        assert mgr.pending_count == 1

        # Third bar triggers expiry (bars_waiting reaches 3)
        result = mgr.manage(bar_high=96.0, bar_low=94.0)
        assert len(result.expired) == 1
        assert mgr.pending_count == 0

    def test_order_fills_before_timeout(self):
        mgr = OrderManager(JudgmentConfig(order_max_wait_bars=5))
        order = _make_order(price=93.0)
        mgr.add_order(order)

        # Wait 2 bars (price not touched)
        mgr.manage(bar_high=96.0, bar_low=94.0)
        mgr.manage(bar_high=95.5, bar_low=94.5)
        assert mgr.pending_count == 1

        # Fill on 3rd bar (price touched)
        result = mgr.manage(bar_high=96.0, bar_low=92.0)
        assert len(result.filled) == 1


class TestValidityCheck:
    def test_cancelled_when_prediction_drifts(self):
        from core.prediction.predictor import PredictionResult

        mgr = OrderManager(JudgmentConfig())
        order = _make_order(price=95.0, predicted_range=(94.0, 98.0))
        mgr.add_order(order)

        # New prediction moved away from order price
        new_pred = PredictionResult(low=96.0, high=100.0, width=2.0, k_actual=1.5)
        result = mgr.manage(bar_high=97.0, bar_low=96.0, prediction=new_pred)
        assert len(result.cancelled) == 1
        assert result.cancelled[0].reason == "prediction_drift"

    def test_kept_when_prediction_still_covers(self):
        from core.prediction.predictor import PredictionResult

        mgr = OrderManager(JudgmentConfig(order_max_wait_bars=10))
        order = _make_order(price=95.0, predicted_range=(94.0, 98.0))
        mgr.add_order(order)

        new_pred = PredictionResult(low=93.0, high=97.0, width=2.0, k_actual=1.5)
        result = mgr.manage(bar_high=97.0, bar_low=96.0, prediction=new_pred)
        assert len(result.cancelled) == 0
        assert mgr.pending_count == 1

    def test_cancelled_on_signal_reversal(self):
        mgr = OrderManager(JudgmentConfig(order_max_wait_bars=10))
        order = _make_order(price=93.0)  # outside bar range
        mgr.add_order(order)

        signals = BarSignals(exit=True)
        result = mgr.manage(bar_high=96.0, bar_low=94.0, signals=signals)
        assert len(result.cancelled) == 1
        assert result.cancelled[0].reason == "signal_reversal"

    def test_kept_when_no_prediction_available(self):
        mgr = OrderManager(JudgmentConfig(order_max_wait_bars=10))
        order = _make_order(price=95.0)
        mgr.add_order(order)

        result = mgr.manage(bar_high=96.0, bar_low=95.5, prediction=None)
        assert len(result.cancelled) == 0
        assert mgr.pending_count == 1


class TestHasPendingEntry:
    def test_detects_pending_entry(self):
        mgr = OrderManager(JudgmentConfig())
        mgr.add_order(_make_order(source="entry"))
        assert mgr.has_pending_entry is True

    def test_no_pending_entry_for_add_order(self):
        mgr = OrderManager(JudgmentConfig())
        mgr.add_order(_make_order(source="add"))
        assert mgr.has_pending_entry is False

    def test_no_pending_when_empty(self):
        mgr = OrderManager(JudgmentConfig())
        assert mgr.has_pending_entry is False


class TestClear:
    def test_clear_removes_all_orders(self):
        mgr = OrderManager(JudgmentConfig())
        mgr.add_order(_make_order())
        mgr.add_order(_make_order(order_id="test-002"))
        mgr.clear()
        assert mgr.pending_count == 0
