"""实盘 OMS（订单生命周期状态机，第6步）—— 不复用 OrderManager。

模拟 ``OrderManager``（order_manager.py:35-129）是本地 bar_high/low 撮合；实盘 OMS 跟踪
**真实订单生命周期**（NEW→PARTIALLY_FILLED→FILLED / CANCELED / REJECTED），用
``client_order_id`` 幂等防重复下单，支持孤儿单查询（重启时认领/撤残留单，第7步 Runner 用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import LiveOrder, OrderStatus

# 仍视为"未最终成交"的状态（需持续跟踪/可撤销）
_OPEN_STATES = (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)


@dataclass
class LiveOMS:
    """实盘订单管理（内存跟踪，交易所查询对账）。"""

    _orders: dict[str, LiveOrder] = field(default_factory=dict)  # client_order_id → order

    def track(self, order: LiveOrder) -> None:
        """登记新订单（幂等：同 client_order_id 覆盖）。"""
        self._orders[order.client_order_id] = order

    def get(self, client_order_id: str) -> Optional[LiveOrder]:
        return self._orders.get(client_order_id)

    def update_from_exchange(
        self, client_order_id: str, exchange_resp: dict
    ) -> Optional[LiveOrder]:
        """用交易所回报更新订单状态（对账）。订单不存在返回 None。"""
        order = self._orders.get(client_order_id)
        if order is None:
            return None
        if "orderId" in exchange_resp:
            order.exchange_order_id = exchange_resp["orderId"]
        if "status" in exchange_resp:
            order.status = OrderStatus(exchange_resp["status"])
        if "executedQty" in exchange_resp:
            order.filled_qty = float(exchange_resp["executedQty"] or 0)
        if exchange_resp.get("avgPrice"):
            order.avg_fill_price = float(exchange_resp["avgPrice"])
        return order

    @property
    def open_orders(self) -> list[LiveOrder]:
        """未最终成交的订单（NEW / PARTIALLY_FILLED）。"""
        return [o for o in self._orders.values() if o.status in _OPEN_STATES]

    def has_pending_entry(self) -> bool:
        """是否有未成交开仓单（防重复开仓，对应模拟 order_manager.has_pending_entry）。"""
        return any(
            not o.reduce_only and o.status in _OPEN_STATES
            for o in self._orders.values()
        )

    def all_tracked(self) -> list[LiveOrder]:
        return list(self._orders.values())

    def remove(self, client_order_id: str) -> None:
        """移除跟踪（已终结的订单清理）。"""
        self._orders.pop(client_order_id, None)
