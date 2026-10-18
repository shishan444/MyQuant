"""账户只读快照（AccountSnapshot——记账器的只读产出）。

消费方=信号解释器（仓位/权益判定输入）+报告+忠实性断言+未来扩展。
快照不可变：解释器读快照判定，状态变更唯一入口是 Ledger。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSnapshot:
    """持仓字段组快照（全有或全无——与 pt_task 持久化字段组一一对应）。

    sl_price/tp_price 是开仓/重锚时点的披露值（atr/trail 模式防线由
    解释器逐 bar 现算，判定权威不依赖本字段——持久化面语义）。

    Attributes:
        side: "long"|"short"。
        entry_price: 入场价（加仓后为加权均价——重锚）。
        quantity: 持仓数量。
        margin: 占用保证金。
        open_cost: 开仓累计成本（含加仓）。
        sl_price: 止损价快照（开仓时点；None=不可预计算）。
        tp_price: 止盈价快照（开仓时点；None=未声明）。
        trail_peak: trail 模式持仓期峰值（多头取 high 最大/空头 low
            最小；记账器逐 bar 更新）。
        entry_bar_seq: 入场成交 bar 序号（max_hold 计数基准）。
        funding_paid: 当前持仓累计资金费净支出（带符号：付出为正/
            收到为负；金额防线判定的资金费分量——与账户级
            total_funding 两口径并存）。
    """

    side: str
    entry_price: float
    quantity: float
    margin: float
    open_cost: float
    sl_price: float | None
    tp_price: float | None
    trail_peak: float
    entry_bar_seq: int
    funding_paid: float = 0.0


@dataclass(frozen=True)
class AccountSnapshot:
    """账户快照（解释器与断言的只读输入）。

    Attributes:
        equity: 总权益（balance+position.margin+未实现盈亏，逐仓口径
            ——open 划出的保证金计回；空仓=balance）。
        balance: 账户余额（保证金外可用+已释放）。
        unrealized_pnl: 未实现盈亏（按 mark_price）。
        position: 持仓字段组；None=空仓。
        bars_held: 已持有根数（当前 bar−入场 bar+1；空仓 0）。
        position_notional: 持仓名义（quantity×mark_price；空仓 0）。
    """

    equity: float
    balance: float
    unrealized_pnl: float
    position: PositionSnapshot | None
    bars_held: int
    position_notional: float
