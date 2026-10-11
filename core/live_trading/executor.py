"""实盘交易执行器（第7步核心）—— 单 bar 决策→风控→下单→SL/TP 闭环。

把 pipeline(决策) + risk(风控) + gateway(下单) + oms(订单) 串成单 bar 执行闭环。
**可测核心**：``execute_closed_bar`` 纯逻辑（mock 组件即可测）。
不含：bar 轮询循环/持久化/心跳（Runner 编排层，依赖 DB 第9步）。

SL/TP v1 用 **pct mode**（``stop_loss``/``take_profit`` 作百分比）；atr mode 后续支持。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from core.strategy.dna import StrategyDNA

from .gateway import BinanceDemoGateway
from .oms import LiveOMS
from .pipeline import LivePipeline
from .risk_guard import RiskGuard
from .types import LiveAccountState, LiveOrder, OrderAction

logger = logging.getLogger(__name__)


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

    def __post_init__(self) -> None:
        """交易链兼容性插桩（0.4.2 R2/D3：装载即拒——在挂载 SL/TP 单
        之前）。live 链 SL/TP v1 仅 pct：atr 倍数按 pct 挂单将产出
        sl=entry×(1−stop_loss) 的负价无效止损单、trail 无峰值回撤实现
        ——两者装载即拒；分批角色基因同拒（无执行能力）。
        """
        from core.strategy.validator import validate_trading_compatibility
        reasons = validate_trading_compatibility(self.dna, "live")
        if reasons:
            raise ValueError(
                "实盘链不支持该策略（DNA 装载即拒）: " + "; ".join(reasons))

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

        # 开仓挂托管 SL/TP（fail-close 安全底线，review-042 CARD-02）：
        # 市价开仓已成交后挂保护单失败（含 gateway 拒单/网络错误/参数
        # 异常）绝不留下无保护持仓——立即反向市价平仓兜底，再 re-raise
        # 中断调用方（Runner 停止该策略）；若平仓也失败，仓位在交易所
        # 侧裸露（无 SL、无平仓单），标 CRITICAL 供告警通道拾取，仍
        # re-raise。partial 妥协（只记录不处理）在此不可接受：无 SL
        # 持仓暴露在无界下行风险中
        if action in (OrderAction.OPEN_LONG, OrderAction.OPEN_SHORT):
            try:
                self._attach_sl_tp(action, close_price)
            except Exception as exc:
                close_action = (OrderAction.CLOSE_LONG
                                if action == OrderAction.OPEN_LONG
                                else OrderAction.CLOSE_SHORT)
                logger.error(
                    "开仓后挂 SL/TP 失败——立即反向平仓兜底: symbol=%s "
                    "entry_order_id=%s qty=%s 挂单失败原因=%r",
                    self.symbol, order.exchange_order_id, qty, exc)
                try:
                    close_order = self.gateway.place_market_order(
                        self.symbol, close_action, qty)
                    self.oms.track(close_order)
                except Exception as close_exc:
                    logger.critical(
                        "裸仓告警：挂 SL/TP 失败且反向平仓亦失败——仓位"
                        "无任何保护，须人工介入: symbol=%s entry_order_id"
                        "=%s qty=%s 平仓失败原因=%r",
                        self.symbol, order.exchange_order_id, qty,
                        close_exc)
                raise

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
