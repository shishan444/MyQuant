
import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
"""第6步 LiveOMS 单元测试（订单生命周期状态机）。"""
import pytest

from core.live_trading.oms import LiveOMS
from core.live_trading.types import (
    LiveOrder,
    OrderAction,
    OrderSide,
    OrderStatus,
    OrderType,
)


def _order(cid: str = "c1", action=OrderAction.OPEN_LONG, reduce=False, status=OrderStatus.NEW) -> LiveOrder:
    return LiveOrder(
        client_order_id=cid,
        symbol="BTCUSDT",
        action=action,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.1,
        reduce_only=reduce,
        status=status,
    )


class TestTrackAndGet:
    def test_track_and_get(self):
        oms = LiveOMS()
        o = _order()
        oms.track(o)
        assert oms.get("c1") is o

    def test_get_missing(self):
        assert LiveOMS().get("nope") is None

    def test_track_idempotent(self):
        oms = LiveOMS()
        oms.track(_order())
        oms.track(_order())  # 覆盖
        assert len(oms.all_tracked()) == 1


class TestUpdateFromExchange:
    def test_updates_status_qty_price(self):
        oms = LiveOMS()
        oms.track(_order())
        updated = oms.update_from_exchange(
            "c1",
            {"orderId": 999, "status": "FILLED", "executedQty": "0.1", "avgPrice": "50100.5"},
        )
        assert updated is not None
        assert updated.exchange_order_id == 999
        assert updated.status == OrderStatus.FILLED
        assert updated.filled_qty == 0.1
        assert updated.avg_fill_price == 50100.5

    def test_unknown_order_returns_none(self):
        assert LiveOMS().update_from_exchange("x", {"status": "FILLED"}) is None

    def test_partial_fill(self):
        oms = LiveOMS()
        oms.track(_order())
        oms.update_from_exchange("c1", {"status": "PARTIALLY_FILLED", "executedQty": "0.05"})
        assert oms.get("c1").status == OrderStatus.PARTIALLY_FILLED
        assert oms.get("c1").filled_qty == 0.05


class TestOpenOrders:
    def test_open_orders_filters_terminal(self):
        oms = LiveOMS()
        oms.track(_order("c1", status=OrderStatus.NEW))
        oms.track(_order("c2", status=OrderStatus.FILLED))
        oms.track(_order("c3", status=OrderStatus.PARTIALLY_FILLED))
        oms.track(_order("c4", status=OrderStatus.CANCELED))
        open_ids = {o.client_order_id for o in oms.open_orders}
        assert open_ids == {"c1", "c3"}

    def test_has_pending_entry(self):
        oms = LiveOMS()
        assert oms.has_pending_entry() is False
        oms.track(_order("c1", action=OrderAction.OPEN_LONG, reduce=False, status=OrderStatus.NEW))
        assert oms.has_pending_entry() is True

    def test_pending_close_does_not_count_as_entry(self):
        oms = LiveOMS()
        oms.track(_order("c1", action=OrderAction.CLOSE_LONG, reduce=True, status=OrderStatus.NEW))
        assert oms.has_pending_entry() is False  # 平仓单不算开仓挂单


class TestRemove:
    def test_remove(self):
        oms = LiveOMS()
        oms.track(_order())
        oms.remove("c1")
        assert oms.get("c1") is None
        oms.remove("c1")  # 不存在不报错
