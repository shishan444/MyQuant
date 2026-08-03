"""实盘交易数据类（第3步，独立于 core/trading/types.py）。

不复用 VirtualAccount 的 Position（那是本地记账模型）。实盘持仓状态以交易所为准
（LiveAccount + sync_state）。One-Way 单向模型：任一时刻一个方向。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderAction(str, Enum):
    """策略意图 → Gateway 动作（One-Way 单向模型）。

    反手（持多遇空信号等）不在此列——反手由 pipeline 拆成"先平后开"两步，
    与模拟交易 judgment.py:72 语义一致。
    """

    OPEN_LONG = "open_long"  # 开多 BUY
    CLOSE_LONG = "close_long"  # 平多 SELL + reduceOnly
    OPEN_SHORT = "open_short"  # 开空 SELL
    CLOSE_SHORT = "close_short"  # 平空 BUY + reduceOnly


class OrderType(str, Enum):
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# One-Way 下单矩阵：动作 → (side, reduceOnly)
# positionSide=BOTH（One-Way 固定），不在此表（Gateway 统一加）
ACTION_TO_SIDE: dict[OrderAction, tuple[OrderSide, bool]] = {
    OrderAction.OPEN_LONG: (OrderSide.BUY, False),
    OrderAction.CLOSE_LONG: (OrderSide.SELL, True),
    OrderAction.OPEN_SHORT: (OrderSide.SELL, False),
    OrderAction.CLOSE_SHORT: (OrderSide.BUY, True),
}


@dataclass
class LiveOrder:
    """实盘订单（本地记录 + 交易所回报）。"""

    client_order_id: str
    symbol: str
    action: OrderAction
    side: OrderSide
    order_type: OrderType
    quantity: float
    reduce_only: bool = False
    price: Optional[float] = None  # 限价 / 止损触发价
    exchange_order_id: Optional[int] = None
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    is_stop_loss: bool = False  # 托管止损单标记
    is_take_profit: bool = False  # 托管止盈单标记


@dataclass
class LivePosition:
    """实盘持仓（交易所为准，sync 后填充）。One-Way 单向：side ∈ {long, short}。"""

    symbol: str
    side: str  # "long" | "short" | ""
    quantity: float  # 绝对值
    entry_price: float
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    margin: float = 0.0
    bars_held: int = 0
    sl_order_id: Optional[int] = None  # 托管止损单交易所 ID（平仓撤单用）
    tp_order_id: Optional[int] = None  # 托管止盈单交易所 ID

    @property
    def is_long(self) -> bool:
        return self.side == "long"

    @property
    def is_short(self) -> bool:
        return self.side == "short"

    @property
    def has_position(self) -> bool:
        return self.side in ("long", "short") and self.quantity > 0


@dataclass
class LiveAccountState:
    """实盘账户状态（交易所为准）。One-Way 单仓。"""

    balance: float = 0.0  # 可用 USDT
    equity: float = 0.0  # 账户权益
    position: Optional[LivePosition] = None
    allowed_direction: str = "mixed"  # DNA.direction（long/short/mixed）
