"""成交方式接口（FillModel）与机械市价实现（MarketFill）。

FillModel.fill 是把意图变成成交的唯一通道（未来限价入 DNA 时加
LimitFill 实现类，接口与调用方零改动）。MarketFill 第一版唯一实现：
信号/时间类意图按执行 bar 开盘价、SL/TP/清算即时类按意图携带的
触发价成交（清算 p_liq 与 SL/TP 触发价同属「触发价成交」语义——
spec 清算条款「清算成交价=触发价」；滑点形制对齐回测 vbt order
slippage：成交价向不利方向偏移——买入贵/卖出便宜，多空两侧同规）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.paper_trading.kernel.types import (
    IMMEDIATE_KINDS,
    ClosedBar,
    Fill,
    Intent,
)

# 入场族 kind（方向=目标方向）；出场族方向=持仓方向
_ENTRY_SIDE_KINDS = frozenset({"open", "add"})


class FillModel(ABC):
    """成交方式接口（可替换的成交抽象）。"""

    @abstractmethod
    def fill(self, intent: Intent, exec_bar: ClosedBar,
             quantity: float) -> Fill:
        """意图+执行 bar+数量 → 成交（唯一的成交产生通道）。

        Args:
            intent: 待成交意图（即时类须携带 trigger_price）。
            exec_bar: 承载执行的 bar（信号/时间类取其开盘价）。
            quantity: 成交数量（记账器定仓/出场量计算后传入）。

        Returns:
            Fill: 含滑点偏移成交价与费用的成交记录。
        """


class MarketFill(FillModel):
    """机械市价成交（第一版唯一实现）。

    成交价规则：信号类（open/close/add/reduce）与时间类
    （exit_max_hold/exit_time）按执行 bar 开盘价；即时类
    （IMMEDIATE_KINDS 单源：exit_sl/exit_tp/exit_liquidation）按意图
    触发价。滑点=成交价向不利方向偏移（买入 ×(1+slip)/卖出
    ×(1−slip)——与持仓方向无关，由资金动作决定；对齐回测引擎 vbt
    order slippage 形制）。
    """

    def __init__(self, fee_rate: float, slippage_rate: float):
        """初始化费滑参数（任务级覆写面，缺省取 constants 单源）。

        Args:
            fee_rate: 手续费率（按成交名义计费）。
            slippage_rate: 滑点率（成交价不利偏移比例）。
        """
        self.fee_rate = float(fee_rate)
        self.slippage_rate = float(slippage_rate)

    def fill(self, intent: Intent, exec_bar: ClosedBar,
             quantity: float) -> Fill:
        base_price = self._base_price(intent, exec_bar)
        price = self._apply_slippage(intent, base_price)
        fee = self.fee_rate * quantity * price
        slippage_cost = abs(price - base_price) * quantity
        return Fill(
            intent=intent, price=price, quantity=quantity,
            fee=fee, slippage_cost=slippage_cost)

    @staticmethod
    def _base_price(intent: Intent, exec_bar: ClosedBar) -> float:
        """基准价分派：即时类按触发价、其余按执行 bar 开盘价。"""
        if intent.kind in IMMEDIATE_KINDS:
            return float(intent.trigger_price)
        return float(exec_bar.open)

    def _apply_slippage(self, intent: Intent, base_price: float) -> float:
        """滑点偏移：买入 ×(1+slip)、卖出 ×(1−slip)（不利方向）。"""
        if self._is_buy(intent):
            return base_price * (1.0 + self.slippage_rate)
        return base_price * (1.0 - self.slippage_rate)

    @staticmethod
    def _is_buy(intent: Intent) -> bool:
        """资金动作方向：开/加仓按目标方向，出场族按持仓方向反转。

        多头开仓/空头平仓=买入；空头开仓/多头平仓=卖出。
        """
        if intent.kind in _ENTRY_SIDE_KINDS:
            return intent.direction == "long"
        return intent.direction == "short"
