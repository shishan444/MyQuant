"""实盘交易数据类（第3步，独立于 core/trading/types.py）。

不复用 VirtualAccount 的 Position（那是本地记账模型）。实盘持仓状态以交易所为准
（LiveAccount + sync_state）。One-Way 单向模型：任一时刻一个方向。

judgment 词汇四类型（Decision/AccountState/JudgmentConfig/BarSignals）自
core/trading/types.py 逐字迁入（rebuild-paper-trading 6.1——旧域退役前置，
live 切自有域）。
"""
from __future__ import annotations

import math
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


# ---------------------------------------------------------------------------
# Judgment 词汇（自 core/trading/types.py 逐字迁入，rebuild-paper-trading 6.1）
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """Output of the judgment rule evaluation.

    action: "hold" | "open" | "close" | "add" | "reduce"
    direction: "long" | "short" | ""
    target_position_pct: fraction of equity for the target position (0.0 ~ 1.0)
    reason: human-readable explanation for logging
    """

    action: str
    direction: str = ""
    target_position_pct: float = 0.0
    entry_size_pct: float = 0.0
    reason: str = ""


@dataclass
class AccountState:
    """Snapshot of the virtual account for judgment rule evaluation."""

    balance: float
    has_position: bool
    position_side: str  # "long" | "short" | "flat"
    position_entry: float
    position_quantity: float
    position_margin: float
    unrealized_pnl: float
    position_bars_held: int  # number of bars since position opened
    target_position_pct: float  # current target set by judgment engine
    actual_position_pct: float  # current position as fraction of equity
    equity: float
    allowed_direction: str = "mixed"  # DNA direction constraint: "long" | "short" | "mixed"


@dataclass
class JudgmentConfig:
    """Configuration for judgment rules and order management."""

    min_hold_bars: int = 3  # minimum bars before allowing close
    min_profit_ratio: float = 2.0  # minimum pnl / fee ratio to allow close
    initial_entry_pct: float = 0.33  # first entry as fraction of target
    profit_add_only: bool = True  # only add when unrealized pnl > 0
    max_hold_bars: int | None = None  # force close if losing and held too long
    max_fill_bars: int = 3  # max consecutive no-signal auto-fill bars
    fee_rate: float = 0.001  # trading fee rate for min_profit calculation
    confidence_sizing_enabled: bool = False  # scale entry by MTF confidence

    # Predictive order management
    use_limit_orders: bool = False  # False=market execution, True=limit orders
    pricing_alpha_base: float = 0.3  # base aggressiveness for limit price
    pricing_alpha_range: float = 0.5  # confidence-adjusted range
    pricing_min_fill_prob: float = 0.3  # minimum fill probability threshold
    order_max_wait_bars: int = 5  # max bars before order expires


@dataclass
class BarSignals:
    """Signals for a single bar (last bar extracted from SignalSet)."""

    entry: bool = False
    exit: bool = False
    add: bool = False
    reduce: bool = False
    direction: float = 1.0  # +1 long, -1 short
    confidence: float = 1.0  # MTF confidence [0.1, 1.0], default 1.0 (no effect)

    @staticmethod
    def from_signal_set(sig_set, idx: int) -> BarSignals:
        """Extract signals for a specific bar index from a SignalSet."""
        raw_direction = (
            float(sig_set.entry_direction.iloc[idx])
            if sig_set.entry_direction is not None
            else 1.0
        )
        # NaN protection: suppress entry when direction is invalid
        entry = bool(sig_set.entries.iloc[idx])
        if entry and math.isnan(raw_direction):
            entry = False
            raw_direction = 0.0

        # Extract confidence if available
        raw_confidence = 1.0
        sig_confidence = getattr(sig_set, "confidence", None)
        if sig_confidence is not None:
            val = float(sig_confidence.iloc[idx])
            raw_confidence = val if not math.isnan(val) else 1.0

        return BarSignals(
            entry=entry,
            exit=bool(sig_set.exits.iloc[idx]),
            add=bool(sig_set.adds.iloc[idx]),
            reduce=bool(sig_set.reduces.iloc[idx]),
            direction=raw_direction,
            confidence=raw_confidence,
        )
