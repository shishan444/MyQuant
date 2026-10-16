"""实盘决策 pipeline（第6步）—— 单 bar 决策翻译，复用 judgment 纯计算。

职责：``sig_set`` + ``bar_idx`` + ``LiveAccountState`` → ``OrderAction``(或 None=hold)。
- 复用自有域 ``core.live_trading.judgment.evaluate``（决策纯函数，rebuild-paper-trading
  6.1 自旧 core/trading 逐字迁入——旧域退役前置）。
- 复用自有域 ``core.live_trading.types.BarSignals``（信号提取）。
- 适配：``LiveAccountState`` → ``AccountState``（字段映射）。
- 翻译：``Decision`` → ``OrderAction``（v1 仅 open/close/hold；add/reduce→None，加减仓后续）。

不含：数据拉取/轮询（Runner 第7步）、下单（Gateway 第4步）、风控（RiskGuard 第5步）。
**时序保证（shift 等价）**：调用方保证 ``sig_set`` 仅含闭合 bar 及之前数据（实盘 bar 闭合触发，
信号只用已闭合数据，等价 shift(1)）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .judgment import evaluate
from .types import (
    AccountState,
    BarSignals,
    Decision,
    JudgmentConfig,
    LiveAccountState,
    OrderAction,
)


def to_judgment_state(live: LiveAccountState) -> AccountState:
    """LiveAccountState → AccountState（字段适配，复用 judgment）。"""
    pos = live.position
    has = bool(pos and pos.has_position)
    return AccountState(
        balance=live.balance,
        has_position=has,
        position_side=pos.side if has else "flat",
        position_entry=pos.entry_price if pos else 0.0,
        position_quantity=pos.quantity if pos else 0.0,
        position_margin=pos.margin if pos else 0.0,
        unrealized_pnl=pos.unrealized_pnl if pos else 0.0,
        position_bars_held=pos.bars_held if pos else 0,
        target_position_pct=0.0,
        actual_position_pct=(pos.margin / live.equity)
        if (has and live.equity > 0)
        else 0.0,
        equity=live.equity,
        allowed_direction=live.allowed_direction,
    )


def decision_to_action(decision: Decision, current_side: str) -> Optional[OrderAction]:
    """Decision → OrderAction（v1：open/close/hold；add/reduce→None）。

    current_side: 当前持仓方向（"long"/"short"/""）。close 按当前持仓平。
    反手由 judgment Rule4 两步实现（本 bar close → 下 bar open 反向），与模拟 judgment.py:72 一致。
    """
    if decision.action == "open":
        if decision.direction == "long":
            return OrderAction.OPEN_LONG
        if decision.direction == "short":
            return OrderAction.OPEN_SHORT
        return None
    if decision.action == "close":
        if current_side == "long":
            return OrderAction.CLOSE_LONG
        if current_side == "short":
            return OrderAction.CLOSE_SHORT
        return None
    # add / reduce / hold → v1 不处理
    return None


@dataclass
class LivePipeline:
    """实盘决策 pipeline（单 bar 决策翻译，无状态）。"""

    judgment_config: JudgmentConfig = field(default_factory=JudgmentConfig)

    def decide(
        self, sig_set, bar_idx: int, account_state: LiveAccountState
    ) -> tuple[Optional[OrderAction], Decision, BarSignals]:
        """对闭合 bar 决策。返回 (action|None, decision, signals)。"""
        signals = BarSignals.from_signal_set(sig_set, bar_idx)
        state = to_judgment_state(account_state)
        decision = evaluate(signals, state, self.judgment_config)
        pos = account_state.position
        current_side = pos.side if (pos and pos.has_position) else ""
        action = decision_to_action(decision, current_side)
        return action, decision, signals
