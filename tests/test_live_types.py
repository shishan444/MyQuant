"""第3步 实盘数据类 单元测试（One-Way 单仓 + 下单矩阵）。"""
import pytest

from core.live_trading.types import (
    ACTION_TO_SIDE,
    LiveAccountState,
    LivePosition,
    OrderAction,
    OrderSide,
)


class TestActionMatrix:
    """One-Way 下单矩阵（规划环5·4.1）——核心安全契约。"""

    def test_open_long_buy_no_reduce(self):
        side, reduce = ACTION_TO_SIDE[OrderAction.OPEN_LONG]
        assert side == OrderSide.BUY
        assert reduce is False

    def test_close_long_sell_reduceonly(self):
        side, reduce = ACTION_TO_SIDE[OrderAction.CLOSE_LONG]
        assert side == OrderSide.SELL
        assert reduce is True  # 平仓单必须 reduceOnly，防变反向开仓

    def test_open_short_sell_no_reduce(self):
        side, reduce = ACTION_TO_SIDE[OrderAction.OPEN_SHORT]
        assert side == OrderSide.SELL
        assert reduce is False

    def test_close_short_buy_reduceonly(self):
        side, reduce = ACTION_TO_SIDE[OrderAction.CLOSE_SHORT]
        assert side == OrderSide.BUY
        assert reduce is True

    def test_matrix_covers_all_actions(self):
        # 4 个动作全部有映射
        assert set(ACTION_TO_SIDE.keys()) == set(OrderAction)


class TestLivePosition:
    def test_long_props(self):
        p = LivePosition(symbol="BTCUSDT", side="long", quantity=0.1, entry_price=50000)
        assert p.is_long is True
        assert p.is_short is False
        assert p.has_position is True

    def test_short_props(self):
        p = LivePosition(symbol="BTCUSDT", side="short", quantity=0.1, entry_price=50000)
        assert p.is_short is True
        assert p.is_long is False
        assert p.has_position is True

    def test_no_position(self):
        p = LivePosition(symbol="BTCUSDT", side="", quantity=0, entry_price=0)
        assert p.has_position is False

    def test_zero_qty_no_position(self):
        p = LivePosition(symbol="BTCUSDT", side="long", quantity=0, entry_price=50000)
        assert p.has_position is False


class TestLiveAccountState:
    def test_defaults(self):
        s = LiveAccountState()
        assert s.balance == 0.0
        assert s.position is None
        assert s.allowed_direction == "mixed"
