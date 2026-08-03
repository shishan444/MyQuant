"""实盘交易执行器（第7步核心）—— 单 bar 决策→风控→下单→SL/TP 闭环。

把 pipeline(决策) + risk(风控) + gateway(下单) + oms(订单) 串成单 bar 执行闭环。
**可测核心**：``execute_closed_bar`` 纯逻辑（mock 组件即可测）。
不含：bar 轮询循环/持久化/心跳（Runner 编排层，依赖 DB 第9步）。

SL/TP v1 用 **pct mode**（``stop_loss``/``take_profit`` 作百分比）；atr mode 后续支持。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.strategy.dna import StrategyDNA

from .gateway import BinanceDemoGateway
from .oms import LiveOMS
from .pipeline import LivePipeline
from .risk_guard import RiskGuard
from .types import LiveAccountState, LiveOrder, OrderAction


def compute_sl_tp_pct(
    entry: float, side: str, stop_loss_pct: float, tp_pct: Optional[float]
) -> tuple[float, Optional[float]]:
    """v1 pct mode SL/TP 价计算。

    long: sl = entry*(1-sl_pct), tp = entry*(1+tp_pct)
    short: sl = entry*(1+sl_pct), tp = entry*(1-tp_pct)
    """
    if side == "long":
        sl = entry * (1 - stop_loss_pct)
        tp = entry * (1 + tp_pct) if tp_pct else None
    else:  # short
        sl = entry * (1 + stop_loss_pct)
        tp = entry * (1 - tp_pct) if tp_pct else None
    return sl, tp


@dataclass
class LiveTradingExecutor:
    """单 bar 执行闭环（决策→风控→下单→SL/TP）。组件全部注入可测。"""

    pipeline: LivePipeline
    gateway: BinanceDemoGateway
    risk: RiskGuard
    oms: LiveOMS
    dna: StrategyDNA
    symbol: str
    state: LiveAccountState
    default_entry_pct: float = 0.10  # decision.entry_size_pct 缺失时 fallback

    def execute_closed_bar(
        self, sig_set, bar_idx: int, close_price: float
    ) -> tuple[Optional[OrderAction], Optional[LiveOrder]]:
        """处理闭合 bar。返回 (action|None, order|None)。

        风控拦截时抛 ``RiskViolation``（调用方记录/停止）。
        """
        action, decision, _ = self.pipeline.decide(sig_set, bar_idx, self.state)
        if action is None:
            return None, None

        qty = self._compute_quantity(action, decision.entry_size_pct, close_price)
        if qty <= 0:
            return action, None

        # 风控（可能抛 RiskViolation）
        self.risk.check_order(
            action, qty, close_price, self.state.equity, self._used_margin()
        )

        # 下单
        order = self.gateway.place_market_order(self.symbol, action, qty)
        self.risk.record_order()
        self.oms.track(order)

        # 开仓挂托管 SL/TP
        if action in (OrderAction.OPEN_LONG, OrderAction.OPEN_SHORT):
            self._attach_sl_tp(action, close_price)

        return action, order

    def _compute_quantity(
        self, action: OrderAction, entry_size_pct: float, price: float
    ) -> float:
        if action in (OrderAction.CLOSE_LONG, OrderAction.CLOSE_SHORT):
            pos = self.state.position
            return pos.quantity if (pos and pos.has_position) else 0.0
        pct = entry_size_pct if entry_size_pct > 0 else self.default_entry_pct
        if price <= 0 or self.state.equity <= 0:
            return 0.0
        return (self.state.equity * pct) / price

    def _used_margin(self) -> float:
        pos = self.state.position
        return pos.margin if (pos and pos.has_position) else 0.0

    def _attach_sl_tp(self, action: OrderAction, entry_price: float) -> None:
        rg = self.dna.risk_genes
        side = "long" if action == OrderAction.OPEN_LONG else "short"
        sl_price, tp_price = compute_sl_tp_pct(
            entry_price, side, rg.stop_loss, rg.take_profit
        )
        resp_sl = self.gateway.attach_stop_loss(self.symbol, side, sl_price)
        pos = self.state.position
        if pos:
            pos.sl_price = sl_price
            pos.sl_order_id = resp_sl.get("orderId")
        if tp_price is not None:
            resp_tp = self.gateway.attach_take_profit(self.symbol, side, tp_price)
            if pos:
                pos.tp_price = tp_price
                pos.tp_order_id = resp_tp.get("orderId")
