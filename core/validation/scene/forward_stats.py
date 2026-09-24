"""Forward-looking statistics computation for scene verification."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from core.scoring.statistics import wilson_interval
import pandas as pd

from .base import AggregateStats, HorizonStats


def compute_forward_stats(
    df: pd.DataFrame,
    trigger_bar: int,
    trigger_price: float,
    horizons: List[int],
) -> Dict[int, HorizonStats]:
    """Compute forward-looking metrics for one trigger across multiple horizons.

    Args:
        df: Full indicator-enhanced DataFrame (DatetimeIndex).
        trigger_bar: Integer position of the trigger bar.
        trigger_price: Close price at the trigger bar.
        horizons: List of bar counts to look forward.

    Returns:
        Dict mapping horizon -> HorizonStats.
    """
    result: Dict[int, HorizonStats] = {}
    max_offset = max(horizons)

    for h in horizons:
        start = trigger_bar + 1
        end = start + h
        available = len(df) - start
        is_partial = available < h
        end = min(end, len(df))

        if end <= start:
            result[h] = HorizonStats(
                horizon=h, close_pct=0.0, max_gain_pct=0.0,
                max_loss_pct=0.0, bars_to_peak=0, bars_to_trough=0,
                is_partial=True,
            )
            continue

        window = df.iloc[start:end]

        close_at_h = window.iloc[-1]["close"]
        close_pct = (close_at_h - trigger_price) / trigger_price * 100

        high_max = window["high"].max()
        low_min = window["low"].min()

        max_gain_pct = (high_max - trigger_price) / trigger_price * 100
        max_loss_pct = (low_min - trigger_price) / trigger_price * 100

        # Find offset of peak/trough within the window
        bars_to_peak = int(window["high"].values.argmax()) + 1
        bars_to_trough = int(window["low"].values.argmin()) + 1

        result[h] = HorizonStats(
            horizon=h,
            close_pct=round(close_pct, 4),
            max_gain_pct=round(max_gain_pct, 4),
            max_loss_pct=round(max_loss_pct, 4),
            bars_to_peak=bars_to_peak,
            bars_to_trough=bars_to_trough,
            is_partial=is_partial,
        )

    return result


def aggregate_by_horizon(
    all_stats: Dict[int, List[HorizonStats]],
) -> List[AggregateStats]:
    """Aggregate per-trigger stats into summary statistics per horizon.

    Args:
        all_stats: Dict mapping horizon -> list of HorizonStats for each trigger.

    Returns:
        List of AggregateStats, one per horizon.
    """
    results: List[AggregateStats] = []

    for horizon in sorted(all_stats.keys()):
        stats_list = all_stats[horizon]
        if not stats_list:
            continue

        n = len(stats_list)
        returns = [s.close_pct for s in stats_list]
        gains = [s.max_gain_pct for s in stats_list]
        losses = [s.max_loss_pct for s in stats_list]
        peaks = [s.bars_to_peak for s in stats_list]

        wins = sum(1 for r in returns if r > 0)
        win_rate = wins / n * 100 if n > 0 else 0
        # Wilson 95% CI（小数域）随 win_rate 换算到百分比域
        ci_lo, ci_hi = wilson_interval(wins, n)
        win_rate_ci = [round(ci_lo * 100, 1), round(ci_hi * 100, 1)]
        arr = np.array(returns)

        percentiles: Dict[str, float] = {}
        if len(arr) > 0:
            percentiles = {
                "p10": round(float(np.percentile(arr, 10)), 2),
                "p25": round(float(np.percentile(arr, 25)), 2),
                "p50": round(float(np.percentile(arr, 50)), 2),
                "p75": round(float(np.percentile(arr, 75)), 2),
                "p90": round(float(np.percentile(arr, 90)), 2),
                "min": round(float(np.min(arr)), 2),
                "max": round(float(np.max(arr)), 2),
            }

        distribution = _build_distribution(returns)

        results.append(AggregateStats(
            horizon=horizon,
            total_triggers=n,
            win_rate=round(win_rate, 1),
            win_rate_ci=win_rate_ci,
            avg_return_pct=round(float(np.mean(returns)), 4) if returns else 0,
            median_return_pct=round(float(np.median(returns)), 4) if returns else 0,
            avg_max_gain_pct=round(float(np.mean(gains)), 4) if gains else 0,
            avg_max_loss_pct=round(float(np.mean(losses)), 4) if losses else 0,
            avg_bars_to_peak=round(float(np.mean(peaks)), 1) if peaks else 0,
            distribution=distribution,
            percentiles=percentiles,
        ))

    return results


def _build_distribution(returns: List[float]) -> List[Dict[str, Any]]:
    """Build histogram buckets from return percentages."""
    if not returns:
        return []

    arr = np.array(returns)
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))

    num_buckets = min(10, max(5, len(returns) // 5))
    bucket_width = (max_val - min_val) / num_buckets if max_val != min_val else 2

    buckets: List[Dict[str, Any]] = []
    for i in range(num_buckets):
        start = min_val + i * bucket_width
        end = start + bucket_width

        mask = (
            (arr >= start) & (arr < end) if i < num_buckets - 1
            else (arr >= start) & (arr <= end)
        )
        count = int(mask.sum())
        buckets.append({
            "range": [round(start, 2), round(end, 2)],
            "count": count,
        })

    return buckets
