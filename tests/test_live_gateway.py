"""第4步 BinanceDemoGateway 单元测试（mock client，不连实盘）。

验证 One-Way 下单矩阵（4 动作）、精度取整、reduceOnly、托管止损止盈 side/closePosition。
"""
import pytest

from core.live_trading.binance_client import BinanceDemoClient
from core.live_trading.gateway import (
    POSITION_SIDE_ONEWAY,
    BinanceDemoGateway,
    GatewayError,
    close_side_for,
)
from core.live_trading.types import OrderAction, OrderSide


class FakeClient:
    """模拟 binance.Client（Gateway 经 BinanceDemoClient 调用）。"""

    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.cancel_calls: list[dict] = []
        self._open_orders: list[dict] = []
        self._positions: list[dict] = []
        self._oid = 100

    def futures_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }

    def futures_create_order(self, **params):
        self.create_calls.append(params)
        self._oid += 1
        return {
            "orderId": self._oid,
            "status": "NEW",
            "executedQty": "0",
            "avgPrice": "0",
        }

    def futures_cancel_order(self, **params):
        self.cancel_calls.append(params)
        return {}

    def futures_get_open_orders(self, **params):
        return self._open_orders

    def futures_position_information(self, **params):
        return self._positions


@pytest.fixture
def gw():
    fake = FakeClient()
    client = BinanceDemoClient(client=fake)
    return BinanceDemoGateway(client), fake


class TestCloseSideFor:
    def test_long_closes_sell(self):
        assert close_side_for("long") == OrderSide.SELL

    def test_short_closes_buy(self):
        assert close_side_for("short") == OrderSide.BUY

    def test_no_position_raises(self):
        with pytest.raises(GatewayError):
            close_side_for("")


class TestPlaceMarketOrder:
    def test_open_long(self, gw):
        g, fake = gw
        order = g.place_market_order("BTCUSDT", OrderAction.OPEN_LONG, 0.123456)
        p = fake.create_calls[-1]
        assert p["side"] == "BUY"
        assert p["positionSide"] == POSITION_SIDE_ONEWAY
        assert p["type"] == "MARKET"
        assert "reduceOnly" not in p
        assert order.reduce_only is False
        assert order.action == OrderAction.OPEN_LONG

    def test_close_long_reduceonly(self, gw):
        g, fake = gw
        g.place_market_order("BTCUSDT", OrderAction.CLOSE_LONG, 0.1)
        p = fake.create_calls[-1]
        assert p["side"] == "SELL"
        assert p["reduceOnly"] == "true"

    def test_open_short(self, gw):
        g, fake = gw
        g.place_market_order("BTCUSDT", OrderAction.OPEN_SHORT, 0.1)
        p = fake.create_calls[-1]
        assert p["side"] == "SELL"
        assert "reduceOnly" not in p

    def test_close_short_reduceonly(self, gw):
        g, fake = gw
        g.place_market_order("BTCUSDT", OrderAction.CLOSE_SHORT, 0.1)
        p = fake.create_calls[-1]
        assert p["side"] == "BUY"
        assert p["reduceOnly"] == "true"

    def test_qty_rounded_to_step(self, gw):
        g, fake = gw
        g.place_market_order("BTCUSDT", OrderAction.OPEN_LONG, 0.123456)
        assert fake.create_calls[-1]["quantity"] == 0.123  # step 0.001

    def test_zero_qty_after_round_raises(self, gw):
        g, _ = gw
        with pytest.raises(GatewayError):
            g.place_market_order("BTCUSDT", OrderAction.OPEN_LONG, 0.0001)  # 取整为 0

    def test_client_order_id_generated(self, gw):
        g, _ = gw
        order = g.place_market_order("BTCUSDT", OrderAction.OPEN_LONG, 0.1)
        assert order.client_order_id.startswith("mq_open_long_")


class TestStopLossTakeProfit:
    def test_attach_stop_loss_long(self, gw):
        g, fake = gw
        g.attach_stop_loss("BTCUSDT", "long", 49999.74)
        p = fake.create_calls[-1]
        assert p["type"] == "STOP_MARKET"
        assert p["side"] == "SELL"  # 多仓止损=卖出平多
        assert p["positionSide"] == POSITION_SIDE_ONEWAY
        assert p["closePosition"] == "true"
        assert p["stopPrice"] == 49999.7  # tick 0.1 取整

    def test_attach_take_profit_short(self, gw):
        g, fake = gw
        g.attach_take_profit("BTCUSDT", "short", 45000.06)
        p = fake.create_calls[-1]
        assert p["type"] == "TAKE_PROFIT_MARKET"
        assert p["side"] == "BUY"  # 空仓止盈=买入平空
        assert p["closePosition"] == "true"
        assert p["stopPrice"] == 45000.1  # tick 0.1 取整（避开 .5 边界）


class TestCancelAndQuery:
    def test_cancel(self, gw):
        g, fake = gw
        g.cancel_order("BTCUSDT", 123)
        assert fake.cancel_calls[-1] == {"symbol": "BTCUSDT", "orderId": 123}

    def test_get_position_raw(self, gw):
        g, fake = gw
        fake._positions = [
            {"symbol": "BTCUSDT", "positionAmt": "0.1"},
            {"symbol": "ETHUSDT", "positionAmt": "0"},
        ]
        pos = g.get_position_raw("BTCUSDT")
        assert pos is not None
        assert pos["symbol"] == "BTCUSDT"

    def test_get_position_raw_none_when_flat(self, gw):
        g, fake = gw
        fake._positions = [{"symbol": "BTCUSDT", "positionAmt": "0"}]
        assert g.get_position_raw("BTCUSDT") is None
