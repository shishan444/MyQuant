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
# Judgment 词汇（自 core/trading/types.py 迁入，rebuild-paper-trading 6.1；
# 代码逐字保留，注释经 CARD-08 翻译）
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """判定规则（judgment.evaluate）的输出。

    action: "hold" | "open" | "close" | "add" | "reduce"
    direction: "long" | "short" | ""
    target_position_pct: 目标仓位占权益比例（0.0 ~ 1.0）
    entry_size_pct: 本笔入场占目标仓位的比例
    reason: 人类可读的解释字符串（日志消费，运行时值由 evaluate 产出）
    """

    action: str
    direction: str = ""
    target_position_pct: float = 0.0
    entry_size_pct: float = 0.0
    reason: str = ""


@dataclass
class AccountState:
    """判定规则评估用的账户快照（只读输入；live 侧由 LiveAccountState 适配而来）。"""

    balance: float
    has_position: bool
    position_side: str  # 取值 "long" | "short" | "flat"（无仓=flat）
    position_entry: float
    position_quantity: float
    position_margin: float
    unrealized_pnl: float
    position_bars_held: int  # 开仓以来经过的 bar 数
    target_position_pct: float  # 判定引擎当前设定的目标仓位
    actual_position_pct: float  # 当前仓位占权益比例
    equity: float
    allowed_direction: str = "mixed"  # DNA direction 约束："long" | "short" | "mixed"


@dataclass
class JudgmentConfig:
    """判定规则与订单管理的配置（字段缺省值即生产默认口径）。"""

    min_hold_bars: int = 3  # 允许平仓前的最少持有 bar 数
    min_profit_ratio: float = 2.0  # 允许平仓的最小 pnl/手续费 倍数门槛
    initial_entry_pct: float = 0.33  # 首笔入场占目标仓位的比例
    profit_add_only: bool = True  # 仅浮盈（unrealized_pnl > 0）时允许加仓
    max_hold_bars: int | None = None  # 亏损仓持有过久的强平阈值（None=禁用）
    max_fill_bars: int = 3  # 无信号时自动补仓的最大连续 bar 数
    fee_rate: float = 0.001  # 最小盈利校验用的交易费率
    confidence_sizing_enabled: bool = False  # 按 MTF confidence 缩放入场规模

    # 预测性订单管理（限价单族）
    use_limit_orders: bool = False  # False=市价执行，True=限价单
    pricing_alpha_base: float = 0.3  # 限价激进度基准
    pricing_alpha_range: float = 0.5  # confidence 调整的激进度浮动范围
    pricing_min_fill_prob: float = 0.3  # 最小成交概率阈值
    order_max_wait_bars: int = 5  # 订单过期前的最大等待 bar 数


@dataclass
class BarSignals:
    """单 bar 信号（从 SignalSet 按指定 bar 序号提取的布尔/符号快照）。"""

    entry: bool = False
    exit: bool = False
    add: bool = False
    reduce: bool = False
    direction: float = 1.0  # +1 多 / -1 空
    confidence: float = 1.0  # MTF confidence ∈ [0.1, 1.0]，缺省 1.0（无缩放效果）

    @staticmethod
    def from_signal_set(sig_set, idx: int) -> BarSignals:
        """从 SignalSet 提取指定 bar 序号的信号。

        缺失语义：entry_direction 为 None 时 direction 缺省 1.0；
        direction 为 NaN 时压制 entry 并置 direction=0.0（中性防御，
        防误开仓）；confidence 无该列或为 NaN 时回 1.0（无缩放）。
        """
        raw_direction = (
            float(sig_set.entry_direction.iloc[idx])
            if sig_set.entry_direction is not None
            else 1.0
        )
        # NaN 防御：direction 无效（NaN）时压制 entry，防误开仓
        entry = bool(sig_set.entries.iloc[idx])
        if entry and math.isnan(raw_direction):
            entry = False
            raw_direction = 0.0

        # confidence 可选提取（SignalSet 无该列时用缺省 1.0）
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
