"""币安 demo 交易网关（第4步）—— One-Way 下单矩阵 + 托管止损止盈单。

执行翻译层：``OrderAction``(策略意图) → 币安 One-Way 下单参数。
- ``positionSide=BOTH``（One-Way 固定）。
- 平仓单 ``reduceOnly=true``（防变反向开仓）。
- 开仓同挂 ``STOP_MARKET``/``TAKE_PROFIT_MARKET``（``closePosition=true``，交易所托管触发）。
- SL/TP 单 side = 平仓方向（多仓 SELL / 空仓 BUY），与 ``ACTION_TO_SIDE`` 平仓动作一致。

不含：决策逻辑（judgment 共享）、风控拦截（RiskGuard 第5步）、OMS 状态机（第6步）。
"""
from __future__ import annotations

import uuid
from typing import Optional

from .binance_client import BinanceDemoClient
from .types import (
    ACTION_TO_SIDE,
    LiveOrder,
    OrderAction,
    OrderSide,
    OrderStatus,
    OrderType,
)

# One-Way 模式 positionSide 固定值
POSITION_SIDE_ONEWAY = "BOTH"


class GatewayError(RuntimeError):
    pass


def close_side_for(position_side: str) -> OrderSide:
    """持仓方向 → 平仓 side（SL/TP 托管单用）。

    多仓平仓=SELL，空仓平仓=BUY（与 ACTION_TO_SIDE 平仓动作一致）。
    """
    if position_side == "long":
        return OrderSide.SELL
    if position_side == "short":
        return OrderSide.BUY
    raise GatewayError(f"无法为无持仓定 SL/TP side: position_side={position_side}")


class BinanceDemoGateway:
    """One-Way 下单执行网关。"""

    def __init__(self, client: BinanceDemoClient) -> None:
        self._client = client

    def place_market_order(
        self, symbol: str, action: OrderAction, quantity: float
    ) -> LiveOrder:
        """下市价单（开/平仓主路径）。数量按 stepSize 自动取整。"""
        if action not in ACTION_TO_SIDE:
            raise GatewayError(f"未知动作: {action}")
        precision = self._client.load_precision(symbol)
        qty = precision.round_qty(quantity)
        if qty <= 0:
            raise GatewayError(
                f"取整后数量<=0: raw={quantity} step={precision.step_size}"
            )
        side, reduce_only = ACTION_TO_SIDE[action]
        client_id = self._gen_client_id(action)
        params: dict = {
            "symbol": symbol,
            "side": side.value,
            "positionSide": POSITION_SIDE_ONEWAY,
            "type": OrderType.MARKET.value,
            "quantity": qty,
            "newClientOrderId": client_id,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        resp = self._client.raw.futures_create_order(**params)
        return LiveOrder(
            client_order_id=client_id,
            symbol=symbol,
            action=action,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            reduce_only=reduce_only,
            exchange_order_id=resp.get("orderId"),
            status=OrderStatus(resp.get("status", "NEW")),
            filled_qty=float(resp.get("executedQty", 0) or 0),
            avg_fill_price=float(resp["avgPrice"]) if resp.get("avgPrice") else None,
        )

    def attach_stop_loss(
        self, symbol: str, position_side: str, stop_price: float
    ) -> dict:
        """挂托管止损单（开仓后调用）。closePosition=true 全仓平。"""
        side = close_side_for(position_side)
        price = self._client.load_precision(symbol).round_price(stop_price)
        return self._client.raw.futures_create_order(
            symbol=symbol,
            side=side.value,
            positionSide=POSITION_SIDE_ONEWAY,
            type=OrderType.STOP_MARKET.value,
            stopPrice=price,
            closePosition="true",
            workingType="MARK_PRICE",
        )

    def attach_take_profit(
        self, symbol: str, position_side: str, tp_price: float
    ) -> dict:
        """挂托管止盈单。closePosition=true 全仓平。"""
        side = close_side_for(position_side)
        price = self._client.load_precision(symbol).round_price(tp_price)
        return self._client.raw.futures_create_order(
            symbol=symbol,
            side=side.value,
            positionSide=POSITION_SIDE_ONEWAY,
            type=OrderType.TAKE_PROFIT_MARKET.value,
            stopPrice=price,
            closePosition="true",
            workingType="MARK_PRICE",
        )

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._client.raw.futures_cancel_order(symbol=symbol, orderId=order_id)

    def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        if symbol:
            return self._client.raw.futures_get_open_orders(symbol=symbol)
        return self._client.raw.futures_get_open_orders()

    def get_position_raw(self, symbol: str) -> Optional[dict]:
        """查非零持仓（One-Way 单仓）。"""
        for p in self._client.raw.futures_position_information(symbol=symbol):
            if p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0:
                return p
        return None

    @staticmethod
    def _gen_client_id(action: OrderAction) -> str:
        return f"mq_{action.value}_{uuid.uuid4().hex[:8]}"
