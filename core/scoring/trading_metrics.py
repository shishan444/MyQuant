"""Trading performance metrics computed from raw task/trade/snapshot data.

Pure functions — no DB access. DB reads stay in api/db_ext.py; this module
holds the math (equity, win rate, profit factor, max drawdown) as the single
source of truth, avoiding the previous drift where the same formulas lived
only inside api/db_ext.py.

Invariant: formulas are moved verbatim from the previous api/db_ext.py
implementation — no math changes (equity, profit_factor, drawdown unchanged).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence


@dataclass
class TradingMetricsInput:
    """Raw inputs needed to compute paper-trading performance metrics."""

    initial_cash: float
    balance: float
    total_trades: int
    win_count: int
    loss_count: int
    realized_pnl: float
    unrealized_pnl: float
    position_side: Any
    position_margin: float
    close_pnls: Sequence[float]        # pnl of closed trades
    equity_snapshots: Sequence[float]  # equity series for drawdown


def compute_equity(
    balance: float,
    position_side: Any,
    position_margin: float,
    unrealized_pnl: float,
) -> float:
    """equity = balance + margin + unrealized_pnl when in position, else balance.

    Margin is locked collateral; excluding it understates equity while a
    position is open (the previous -100% misreading).
    """
    if not position_side:
        return balance
    return balance + (position_margin or 0.0) + (unrealized_pnl or 0.0)


def compute_trading_metrics(data: TradingMetricsInput) -> Dict[str, Any]:
    """Compute performance metrics from raw data (no DB access).

    Returns total_return / win_rate / profit_factor / max_drawdown / etc.
    Math moved verbatim from the previous api/db_ext.py implementation.
    """
    equity = compute_equity(
        data.balance, data.position_side, data.position_margin, data.unrealized_pnl
    )
    total_pnl = equity - data.initial_cash
    # 零交易 win_rate 为缺失语义（None），不得折叠为 0
    win_rate = None if data.total_trades == 0 else data.win_count / data.total_trades

    # Gross profit / loss from closed-trade pnls
    gross_profit = sum(p for p in data.close_pnls if p > 0)
    gross_loss = sum(p for p in data.close_pnls if p < 0)
    profit_factor = (
        gross_profit / max(abs(gross_loss), 1e-8) if gross_loss != 0 else float("inf")
    )

    # Max drawdown from equity snapshots
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    if data.equity_snapshots:
        peak = data.equity_snapshots[0]
        for eq in data.equity_snapshots:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_pct = dd / peak if peak > 0 else 0.0

    total_return = equity / data.initial_cash - 1 if data.initial_cash > 0 else 0.0
    avg_trade_pnl = total_pnl / max(data.total_trades, 1)

    return {
        "total_return": round(total_return, 6),
        "total_return_pct": round(total_return * 100, 2),
        "win_rate": None if win_rate is None else round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct * 100, 2),
        "avg_trade_pnl": round(avg_trade_pnl, 2),
        "total_trades": data.total_trades,
        "total_pnl": round(total_pnl, 2),
        "realized_pnl": round(data.realized_pnl, 2),
        "unrealized_pnl": round(data.unrealized_pnl, 2),
        "win_count": data.win_count,
        "loss_count": data.loss_count,
    }
