"""开仓挂 SL/TP 失败的 fail-close 兜底（review-042 CARD-02）。

修复前现象：execute_closed_bar 市价开仓成交后 _attach_sl_tp 无
try 包裹——挂单任何失败（gateway 拒单/网络错误/CARD-01 类参数
异常）= 持仓已建成而无 SL 保护 + 异常中断。

fail-close 语义锚组：
- 挂 SL 失败 → 立即反向市价平仓被调用（量=开仓量）
- 原异常传播（调用方停止该策略）
- 反向平仓也失败 → 异常仍传播（CRITICAL 裸仓告警走日志通道）
- SL 挂单成功路径不受影响（回归锚）
"""

from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.integration]

from core.live_trading.executor import LiveTradingExecutor
from core.live_trading.gateway import GatewayError
from core.live_trading.oms import LiveOMS
from core.live_trading.risk_guard import RiskConfig, RiskGuard
from core.live_trading.types import (
    LiveAccountState,
    LiveOrder,
    OrderAction,
    OrderSide,
    OrderStatus,
    OrderType,
)
from core.live_trading.types import Decision


class FakePipeline:
    """可控 pipeline（指定返回 action/decision）。"""

    def __init__(self, action, entry_size_pct=0.1):
        self._action = action
        self._esp = entry_size_pct

    def decide(self, sig_set, bar_idx, state):
        return self._action, Decision(action="open", entry_size_pct=self._esp), None


class FailCloseGateway:
    """可注入挂单失败的 gateway（记录全部下单/挂单调用）。"""

    def __init__(self, sl_exc=None, market_exc=None):
        self.market_calls = []
        self.sl_calls = []
        self.tp_calls = []
        self._sl_exc = sl_exc
        self._market_exc = market_exc  # 仅对第二个市价单（应急平仓）生效
        self._oid = 200

    def place_market_order(self, symbol, action, quantity):
        self.market_calls.append((action, quantity))
        if self._market_exc is not None and len(self.market_calls) == 2:
            raise self._market_exc
        self._oid += 1
        return LiveOrder(
            client_order_id=f"c{self._oid}", symbol=symbol, action=action,
            side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=quantity,
            exchange_order_id=self._oid, status=OrderStatus.NEW,
        )

    def attach_stop_loss(self, symbol, side, stop_price):
        if self._sl_exc is not None:
            raise self._sl_exc
        self._oid += 1
        self.sl_calls.append((side, stop_price))
        return {"orderId": self._oid}

    def attach_take_profit(self, symbol, side, tp_price):
        self._oid += 1
        self.tp_calls.append((side, tp_price))
        return {"orderId": self._oid}


def _dna(sl=0.05, tp=0.10):
    # 装载校验（0.4.2 R2 插桩）访问 risk_genes.sl_mode 与 signal_genes/
    # layers——mock 补齐属性（pct+无分批基因=放行）
    return SimpleNamespace(
        risk_genes=SimpleNamespace(
            stop_loss=sl, take_profit=tp, sl_mode="pct"),
        signal_genes=[], layers=None)


def _executor(gateway):
    return LiveTradingExecutor(
        pipeline=FakePipeline(OrderAction.OPEN_LONG),
        gateway=gateway,
        risk=RiskGuard(config=RiskConfig(max_notional_per_order=100000)),
        oms=LiveOMS(), dna=_dna(), symbol="BTCUSDT",
        state=LiveAccountState(balance=10000, equity=10000),
    )


class TestSlAttachFailClose:
    def test_sl_attach_failure_triggers_reverse_close(self):
        """挂 SL 失败 → 反向市价平仓（CLOSE_LONG，量=开仓量）+ 异常传播。"""
        gw = FailCloseGateway(sl_exc=GatewayError("SL rejected"))
        ex = _executor(gw)
        with pytest.raises(GatewayError, match="SL rejected"):
            ex.execute_closed_bar(None, 0, 50000)
        # 第一单=开仓 0.02（10000×0.1/50000），第二单=反向平仓同量
        assert gw.market_calls == [
            (OrderAction.OPEN_LONG, 0.02), (OrderAction.CLOSE_LONG, 0.02)]

    def test_original_exception_propagates_not_close_failure(self):
        """传播的是挂单原异常（调用方据此停止策略）；平仓单入 OMS 对账。"""
        gw = FailCloseGateway(sl_exc=ValueError("stopPrice not in range"))
        ex = _executor(gw)
        with pytest.raises(ValueError, match="stopPrice"):
            ex.execute_closed_bar(None, 0, 50000)
        close_orders = [o for o in ex.oms._orders.values()
                        if o.action == OrderAction.CLOSE_LONG]
        assert len(close_orders) == 1

    def test_reverse_close_failure_still_raises(self, caplog):
        """平仓也失败：异常仍传播，CRITICAL 裸仓告警落日志通道。"""
        gw = FailCloseGateway(
            sl_exc=GatewayError("SL rejected"),
            market_exc=GatewayError("reduceOnly rejected"))
        ex = _executor(gw)
        with pytest.raises(GatewayError, match="SL rejected"):
            with caplog.at_level("CRITICAL", logger="core.live_trading.executor"):
                ex.execute_closed_bar(None, 0, 50000)
        assert "裸仓" in caplog.text
        # 平仓尝试发生过（开仓 1 次 + 平仓失败 1 次）
        assert len(gw.market_calls) == 2

    def test_short_open_reverse_closes_short(self):
        """开空场景：挂 SL 失败 → CLOSE_SHORT 反向（方向映射锚）。"""
        gw = FailCloseGateway(sl_exc=GatewayError("SL rejected"))
        ex = LiveTradingExecutor(
            pipeline=FakePipeline(OrderAction.OPEN_SHORT),
            gateway=gw,
            risk=RiskGuard(config=RiskConfig(max_notional_per_order=100000)),
            oms=LiveOMS(), dna=_dna(), symbol="BTCUSDT",
            state=LiveAccountState(balance=10000, equity=10000),
        )
        with pytest.raises(GatewayError):
            ex.execute_closed_bar(None, 0, 50000)
        assert gw.market_calls == [
            (OrderAction.OPEN_SHORT, 0.02), (OrderAction.CLOSE_SHORT, 0.02)]

    def test_normal_path_unaffected(self):
        """SL 挂单成功路径回归锚：无异常、挂单照常、无多余平仓单。"""
        gw = FailCloseGateway()
        ex = _executor(gw)
        action, order = ex.execute_closed_bar(None, 0, 50000)
        assert action == OrderAction.OPEN_LONG and order is not None
        assert len(gw.sl_calls) == 1 and len(gw.tp_calls) == 1
        assert gw.market_calls == [(OrderAction.OPEN_LONG, 0.02)]
