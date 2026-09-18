
import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
"""第7步 LiveTradingExecutor 单元测试（单 bar 决策→风控→下单→SL/TP 闭环）。"""
from types import SimpleNamespace

import pytest

from core.live_trading.executor import LiveTradingExecutor, compute_sl_tp_pct
from core.live_trading.gateway import BinanceDemoGateway
from core.live_trading.oms import LiveOMS
from core.live_trading.pipeline import LivePipeline
from core.live_trading.risk_guard import RiskConfig, RiskGuard, RiskViolation
from core.live_trading.types import (
    LiveAccountState,
    LiveOrder,
    LivePosition,
    OrderAction,
    OrderSide,
    OrderStatus,
    OrderType,
)
from core.trading.types import Decision


class FakePipeline:
    """可控 pipeline（指定返回 action/decision）。"""

    def __init__(self, action, entry_size_pct=0.1):
        self._action = action
        self._esp = entry_size_pct

    def decide(self, sig_set, bar_idx, state):
        return self._action, Decision(action="open", entry_size_pct=self._esp), None


class FakeGateway:
    def __init__(self):
        self.market_calls = []
        self.sl_calls = []
        self.tp_calls = []
        self._oid = 200

    def place_market_order(self, symbol, action, quantity):
        self.market_calls.append((action, quantity))
        self._oid += 1
        return LiveOrder(
            client_order_id=f"c{self._oid}", symbol=symbol, action=action,
            side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=quantity,
            exchange_order_id=self._oid, status=OrderStatus.NEW,
        )

    def attach_stop_loss(self, symbol, side, stop_price):
        self._oid += 1
        self.sl_calls.append((side, stop_price))
        return {"orderId": self._oid}

    def attach_take_profit(self, symbol, side, tp_price):
        self._oid += 1
        self.tp_calls.append((side, tp_price))
        return {"orderId": self._oid}


def _dna(sl=0.05, tp=0.10):
    return SimpleNamespace(risk_genes=SimpleNamespace(stop_loss=sl, take_profit=tp))


def _executor(action, state=None, entry_pct=0.1, risk=None):
    state = state or LiveAccountState(balance=10000, equity=10000)
    return LiveTradingExecutor(
        pipeline=FakePipeline(action, entry_pct),
        gateway=FakeGateway(),
        risk=risk or RiskGuard(config=RiskConfig(max_notional_per_order=100000)),
        oms=LiveOMS(),
        dna=_dna(),
        symbol="BTCUSDT",
        state=state,
    ), state


class TestComputeSlTp:
    def test_long(self):
        sl, tp = compute_sl_tp_pct(50000, "long", 0.05, 0.10)
        assert sl == pytest.approx(47500.0)
        assert tp == pytest.approx(55000.0)

    def test_short(self):
        sl, tp = compute_sl_tp_pct(50000, "short", 0.05, 0.10)
        assert sl == pytest.approx(52500.0)
        assert tp == pytest.approx(45000.0)

    def test_no_tp(self):
        sl, tp = compute_sl_tp_pct(50000, "long", 0.05, None)
        assert sl == 47500.0
        assert tp is None


class TestExecuteClosedBar:
    def test_hold_returns_none(self):
        ex, _ = _executor(None)
        action, order = ex.execute_closed_bar(None, 0, 50000)
        assert action is None and order is None

    def test_open_long_places_order_and_sl_tp(self):
        ex, _ = _executor(OrderAction.OPEN_LONG, entry_pct=0.1)
        action, order = ex.execute_closed_bar(None, 0, 50000)
        assert action == OrderAction.OPEN_LONG
        assert order is not None
        # qty = 10000 * 0.1 / 50000 = 0.02
        assert ex.gateway.market_calls == [(OrderAction.OPEN_LONG, 0.02)]
        # 开仓挂 SL/TP
        assert len(ex.gateway.sl_calls) == 1
        assert len(ex.gateway.tp_calls) == 1
        assert ex.gateway.sl_calls[0][0] == "long"  # 多仓 SL side
        # SL 价 = 50000*(1-0.05)=47500
        assert ex.gateway.sl_calls[0][1] == 47500.0

    def test_close_does_not_attach_sl_tp(self):
        pos = LivePosition(symbol="BTCUSDT", side="long", quantity=0.1, entry_price=50000)
        state = LiveAccountState(balance=10000, equity=10000, position=pos)
        ex, _ = _executor(OrderAction.CLOSE_LONG, state=state)
        action, order = ex.execute_closed_bar(None, 0, 51000)
        assert action == OrderAction.CLOSE_LONG
        # 平仓量 = 持仓 0.1
        assert ex.gateway.market_calls == [(OrderAction.CLOSE_LONG, 0.1)]
        # 平仓不挂 SL/TP
        assert ex.gateway.sl_calls == []
        assert ex.gateway.tp_calls == []

    def test_order_tracked_in_oms(self):
        ex, _ = _executor(OrderAction.OPEN_LONG)
        _, order = ex.execute_closed_bar(None, 0, 50000)
        assert ex.oms.get(order.client_order_id) is order

    def test_risk_violation_propagates(self):
        # 日亏熔断
        risk = RiskGuard(config=RiskConfig(daily_loss_limit=100))
        risk.record_pnl(-150)
        ex, _ = _executor(OrderAction.OPEN_LONG, risk=risk)
        with pytest.raises(RiskViolation, match="daily_loss_limit"):
            ex.execute_closed_bar(None, 0, 50000)
        # 风控拦截不下单
        assert ex.gateway.market_calls == []

    def test_zero_qty_no_order(self):
        # equity=0 → qty=0
        state = LiveAccountState(balance=0, equity=0)
        ex, _ = _executor(OrderAction.OPEN_LONG, state=state)
        action, order = ex.execute_closed_bar(None, 0, 50000)
        assert action == OrderAction.OPEN_LONG
        assert order is None
        assert ex.gateway.market_calls == []
