"""Shared helpers for the backtest pipeline (api/routes/strategies.py).

Pure functions extracted verbatim from the 5 duplicated pipeline copies.
Each helper preserves the exact behaviour of its source.

Invariant: no math/logic change — verbatim extraction.

Important constraint: fallback_metrics serves ONLY the BacktestEngine path
(copies 1-4: backtest/compare/verify/_VerifyProcessor). ReplayRunner (copy 5,
_BatchBacktestProcessor) results lack metrics_dict / bars_per_year / trade_returns
attributes, so it must keep its own metrics synthesis — do NOT route it through
fallback_metrics (would change sharpe computation).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def fallback_metrics(bt_result) -> Dict[str, Any]:
    """Return engine-computed metrics, or recompute from the equity curve.

    Used by BacktestEngine-path copies (1-4). The engine pre-fills metrics_dict
    on BacktestResult; compute_metrics is only a fallback when it is falsy.
    """
    from core.scoring.metrics import compute_metrics
    return bt_result.metrics_dict or compute_metrics(
        bt_result.equity_curve,
        total_trades=bt_result.total_trades,
        bars_per_year=bt_result.bars_per_year,
        trade_win_rate=bt_result.trade_win_rate,
        trade_returns=bt_result.trade_returns,
    )


def build_signals_from_trades_df(trades_df) -> List[Dict[str, Any]]:
    """Build response signals from BacktestEngine trades_df (vectorbt columns).

    Returns a flat list of entry/exit signal dicts (for the API response body).
    Source: strategies.py backtest_strategy (was inline ~374-400).
    """
    signals: List[Dict[str, Any]] = []
    for _, trade_row in trades_df.iterrows():
        direction_str = str(trade_row.get("Direction", "Long"))
        if direction_str == "Short":
            entry_type, exit_type = "sell", "buy"
            entry_label, exit_label = "卖出开仓", "买入平仓"
        else:
            entry_type, exit_type = "buy", "sell"
            entry_label, exit_label = "买入开仓", "卖出平仓"
        entry_price = float(trade_row.get("Avg Entry Price", 0))
        exit_price = float(trade_row.get("Avg Exit Price", 0))
        signals.append({
            "type": entry_type,
            "timestamp": str(trade_row.get("Entry Timestamp", "")),
            "price": entry_price,
            "confidence": 0.8,
            "reason": f"{entry_label} @ {entry_price:.2f}",
        })
        signals.append({
            "type": exit_type,
            "timestamp": str(trade_row.get("Exit Timestamp", "")),
            "price": exit_price,
            "confidence": 0.8,
            "reason": f"{exit_label} @ {exit_price:.2f}",
        })
    return signals


def build_signals_from_events(events_log: list) -> Optional[str]:
    """Serialize ReplayRunner position_closed events to a JSON signals string.

    Source: strategies.py _BatchBacktestProcessor._build_signals_json_from_events
    (was ~1381-1401). Returns None when there are no closed positions.
    Note: timestamps are empty (events_log carries no timestamps) — preserved
    from the original.
    """
    closed = [e for e in events_log if e.get("type") == "position_closed"]
    if not closed:
        return None
    signals = []
    for e in closed:
        side = e.get("side", "long")
        entry_type = "sell" if side == "short" else "buy"
        exit_type = "buy" if side == "short" else "sell"
        entry_label = "卖出开仓" if side == "short" else "买入开仓"
        exit_label = "买入平仓" if side == "short" else "卖出平仓"
        signals.append({
            "type": entry_type, "timestamp": "", "price": round(float(e["entry_price"]), 4),
            "confidence": 0.8, "reason": f"{entry_label} @ {e['entry_price']:.2f}",
        })
        signals.append({
            "type": exit_type, "timestamp": "", "price": round(float(e["exit_price"]), 4),
            "confidence": 0.8, "reason": f"{exit_label}({e.get('exit_reason', '')}) @ {e['exit_price']:.2f}",
        })
    return json.dumps(signals)


def compute_needed_tfs(dnas, timeframe: str) -> set:
    """Collect MTF layer timeframes + the exec timeframe for a list of DNAs.

    Returns an empty set when no DNA needs multi-tf loading (no MTF
    layers and no gene-level timeframe; caller then skips load_mtf_data).
    Source: strategies.py copies 2/3/4/5.

    Behaviour preserved: copy 2 originally called load_mtf_data inside
    `if dna.is_mtf` without a `len(needed_tfs) > 1` gate. This helper returns a
    non-empty set iff any DNA is MTF, so `if needed_tfs:` matches that exactly
    (do NOT add a len>1 gate here — it would change copy 2's behaviour).
    """
    needed: set = set()
    for d in dnas:
        # unblock-research-validation 3a：判据单源化——逐 DNA 经研究链
        # 同款收集器（layers ∪ 基因级 timeframe ∪ 执行周期），列表
        # 聚合语义保持（任一 DNA 需装载即装载）
        from core.analysis.backtest_bridge import _collect_needed_tfs
        per = _collect_needed_tfs(d, timeframe)
        needed |= per
    if needed:
        needed.add(timeframe)
    return needed
