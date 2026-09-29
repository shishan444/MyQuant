"""Entry/exit rule evaluation engine for signal generation and trade pairing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.data.storage import load_parquet
from core.features.indicators import compute_all_indicators
from core.validation.engine import _evaluate_single_condition, _resolve_subject, _resolve_target
from core.validation.mtf import load_mtf_data, merge_to_base, get_timeframe_minutes


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RuleSignal:
    """A single entry or exit signal."""
    time: str
    price: float
    type: str  # "buy" or "sell"


@dataclass
class TradeRecord:
    """A completed trade with entry and exit."""
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    return_pct: float
    is_win: bool


@dataclass
class RuleResult:
    """Complete rule evaluation output."""
    buy_signals: List[RuleSignal] = field(default_factory=list)
    sell_signals: List[RuleSignal] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: Optional[float] = None
    total_return_pct: float = 0.0
    avg_return_pct: float = 0.0
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_rules(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    entry_conditions: List[Dict[str, Any]],
    exit_conditions: List[Dict[str, Any]],
    data_dir: Optional[str] = None,
) -> RuleResult:
    """Evaluate entry/exit rule conditions and generate paired trades.

    Args:
        symbol: Trading pair like "BTCUSDT".
        timeframe: Base (execution) timeframe like "4h".
        start: Start date "2024-01-01".
        end: End date "2024-12-31".
        entry_conditions: List of entry condition dicts.
        exit_conditions: List of exit condition dicts.
        data_dir: Optional path to market data directory.

    Returns:
        RuleResult with signals, trades, and statistics.
    """
    warnings: List[str] = []

    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "market")

    # ------------------------------------------------------------------
    # 1. Load base timeframe data
    # ------------------------------------------------------------------
    safe_symbol = re.sub(r"[^A-Za-z0-9]", "", symbol)
    parquet_path = Path(data_dir) / f"{safe_symbol}_{timeframe}.parquet"

    if not parquet_path.exists():
        warnings.append(f"Data file not found: {parquet_path.name}")
        return RuleResult(warnings=warnings)

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        warnings.append("Loaded data is empty")
        return RuleResult(warnings=warnings)

    # Filter by date range
    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]

    if len(df) == 0:
        warnings.append("No data in requested date range")
        return RuleResult(warnings=warnings)

    # ------------------------------------------------------------------
    # 2. Compute indicators for base timeframe
    # ------------------------------------------------------------------
    enhanced_df = compute_all_indicators(df)

    # ------------------------------------------------------------------
    # 3. MTF: discover referenced timeframes and merge higher-TF data
    # ------------------------------------------------------------------
    all_conditions = list(entry_conditions) + list(exit_conditions)
    referenced_tfs: set[str] = set()
    for cond in all_conditions:
        cond_tf = cond.get("timeframe", "")
        if cond_tf and cond_tf != timeframe:
            referenced_tfs.add(cond_tf)

    if referenced_tfs:
        try:
            mtf_data = load_mtf_data(symbol, list(referenced_tfs), start, end, data_dir)
            if mtf_data:
                mtf_with_indicators: Dict[str, pd.DataFrame] = {}
                for tf_key, tf_df in mtf_data.items():
                    mtf_with_indicators[tf_key] = compute_all_indicators(tf_df)

                base_tf_minutes = get_timeframe_minutes(timeframe)
                tf_minutes_map = {tf_key: get_timeframe_minutes(tf_key) for tf_key in mtf_with_indicators}
                enhanced_df = merge_to_base(
                    enhanced_df, mtf_with_indicators,
                    base_tf_minutes, tf_minutes_map,
                )
        except Exception as exc:
            warnings.append(f"MTF data loading failed, falling back to single timeframe: {exc}")

    # ------------------------------------------------------------------
    # 4. Evaluate entry and exit conditions
    # ------------------------------------------------------------------
    entry_mask = _evaluate_rule_conditions(enhanced_df, entry_conditions, warnings)
    exit_mask = _evaluate_rule_conditions(enhanced_df, exit_conditions, warnings)

    # ------------------------------------------------------------------
    # 5. Generate signals
    # ------------------------------------------------------------------
    buy_signals: List[RuleSignal] = []
    sell_signals: List[RuleSignal] = []

    for idx in enhanced_df.index[entry_mask]:
        buy_signals.append(RuleSignal(
            time=str(idx),
            price=float(enhanced_df.loc[idx, "close"]),
            type="buy",
        ))

    for idx in enhanced_df.index[exit_mask]:
        sell_signals.append(RuleSignal(
            time=str(idx),
            price=float(enhanced_df.loc[idx, "close"]),
            type="sell",
        ))

    # ------------------------------------------------------------------
    # 6. Pair entry -> exit into trades
    # ------------------------------------------------------------------
    trades = _pair_trades(enhanced_df, entry_mask, exit_mask)

    # ------------------------------------------------------------------
    # 7. Compute statistics
    # ------------------------------------------------------------------
    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t.is_win)
    loss_trades = total_trades - win_trades
    # 计算层统一小数口径；零交易为缺失语义（None）
    win_rate = round(win_trades / total_trades, 4) if total_trades > 0 else None

    total_return_pct = round(sum(t.return_pct for t in trades), 2)
    avg_return_pct = round(total_return_pct / total_trades, 2) if total_trades > 0 else 0.0

    return RuleResult(
        buy_signals=buy_signals,
        sell_signals=sell_signals,
        trades=trades,
        total_trades=total_trades,
        win_trades=win_trades,
        loss_trades=loss_trades,
        win_rate=win_rate,
        total_return_pct=total_return_pct,
        avg_return_pct=avg_return_pct,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Condition evaluation (fixed AND/OR logic)
# ---------------------------------------------------------------------------

def _evaluate_rule_conditions(
    df: pd.DataFrame,
    conditions: List[Dict[str, Any]],
    warnings: List[str],
) -> pd.Series:
    """Evaluate a list of conditions with per-condition logic operators.

    Unlike the legacy _evaluate_conditions in engine.py which uses the
    *last* condition's logic for all subsequent masks, this implementation
    applies each condition's own ``logic`` field to combine with the
    accumulated result.

    The first condition's logic field is ignored (nothing to combine with).

    IF and AND are treated equivalently at the boolean level.
    """
    if not conditions:
        return pd.Series(False, index=df.index)

    result: Optional[pd.Series] = None

    from core.analysis.condition_kernel import _mask_for
    for i, cond in enumerate(conditions):
        # 0.2.13：两段式（结构化 subject 先按 params 现算再精确列名求值；
        # 列缺失全 False 降级与 _evaluate_single_condition 语义一致）
        mask = _mask_for(df, cond)
        logic = cond.get("logic", "AND") if i > 0 else None

        if result is None:
            result = mask
        elif logic == "OR":
            result = result | mask
        else:
            # AND or IF -- equivalent at boolean level
            result = result & mask

    if result is not None:
        return result.fillna(False)

    return pd.Series(False, index=df.index)


# ---------------------------------------------------------------------------
# Trade pairing
# ---------------------------------------------------------------------------

def _pair_trades(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    exit_mask: pd.Series,
) -> List[TradeRecord]:
    """Pair entry and exit signals into sequential trades.

    Logic:
    1. Find the first entry signal after the current position.
    2. Find the first exit signal *after* that entry.
    3. When both entry and exit fire at the same timestamp, exit takes
       priority (so we close any open position before opening a new one).
    4. Record the trade and resume searching from the bar *after* the exit.
    """
    trades: List[TradeRecord] = []

    entry_indices = set(df.index[entry_mask])
    exit_indices = set(df.index[exit_mask])

    all_index = df.index
    n = len(all_index)

    pos = 0  # current scan position as integer index

    while pos < n:
        # Phase 1: find next entry signal
        entry_loc = None
        for i in range(pos, n):
            if all_index[i] in entry_indices:
                entry_loc = i
                break

        if entry_loc is None:
            break

        entry_time = all_index[entry_loc]
        entry_price = float(df.loc[entry_time, "close"])

        # Phase 2: find next exit signal strictly after entry
        exit_loc = None
        for i in range(entry_loc + 1, n):
            if all_index[i] in exit_indices:
                exit_loc = i
                break

        if exit_loc is None:
            # No exit found -- trade remains open, skip it
            break

        exit_time = all_index[exit_loc]
        exit_price = float(df.loc[exit_time, "close"])

        return_pct = round((exit_price - entry_price) / entry_price * 100, 2)
        is_win = return_pct > 0

        trades.append(TradeRecord(
            entry_time=str(entry_time),
            entry_price=entry_price,
            exit_time=str(exit_time),
            exit_price=exit_price,
            return_pct=return_pct,
            is_win=is_win,
        ))

        # Resume scanning from the bar after the exit
        pos = exit_loc + 1

    return trades
