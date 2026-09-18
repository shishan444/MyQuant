"""Multi-timeframe data loading and merging."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from core.data.storage import load_parquet
from core.data.timeframes import TF_SECONDS

# B4.1 归一：分钟表由统一秒表推导（消除第三张 TF 表）
_TF_MINUTES: Dict[str, int] = {tf: sec // 60 for tf, sec in TF_SECONDS.items()}


def get_timeframe_minutes(tf: str) -> int:
    """Convert timeframe string to minutes."""
    tf_lower = tf.lower().strip()
    if tf_lower in _TF_MINUTES:
        return _TF_MINUTES[tf_lower]

    # Fallback: try parsing "Nh", "Nm", "Nd", "Nw"
    match = re.match(r"(\d+)\s*(m|h|d|w)", tf_lower)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        multiplier = {"m": 1, "h": 60, "d": 1440, "w": 10080}
        return amount * multiplier[unit]

    raise ValueError(f"Unknown timeframe: {tf}")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_mtf_data(
    pair: str,
    timeframes: List[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """Load parquet data for multiple timeframes.

    Returns:
        Dict mapping timeframe string to its DataFrame.
    """
    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "market")

    safe_symbol = re.sub(r"[^A-Za-z0-9]", "", pair)
    result: Dict[str, pd.DataFrame] = {}

    from core.data.mtf_loader import find_parquet  # B4.1：共享路径发现（含别名兜底）

    for tf in timeframes:
        path = find_parquet(Path(data_dir), safe_symbol, tf)
        if path is None:
            continue

        df = load_parquet(path)
        if df is None or len(df) == 0:
            continue

        # Filter by date range
        if start:
            df = df[df.index >= start]
        if end:
            df = df[df.index <= end]

        if len(df) > 0:
            result[tf] = df

    return result


# ---------------------------------------------------------------------------
# Merge to base timeframe
# ---------------------------------------------------------------------------

def merge_to_base(
    base_df: pd.DataFrame,
    info_dfs: Dict[str, pd.DataFrame],
    base_tf_minutes: int,
    info_tf_minutes_map: Optional[Dict[str, int]] = None,
) -> pd.DataFrame:
    """Merge higher-timeframe indicator columns into base DataFrame.

    Uses forward-fill via pd.merge_asof with time alignment.
    High-timeframe timestamps are shifted forward by one base_tf unit
    to prevent look-ahead bias.

    Column naming: ema_20 -> ema_20_4h, bb_upper_20_2.0 -> bb_upper_4h
    """
    result = base_df.copy()

    for tf, info_df in info_dfs.items():
        if info_df.empty:
            continue

        tf_minutes = (
            info_tf_minutes_map.get(tf, get_timeframe_minutes(tf))
            if info_tf_minutes_map
            else get_timeframe_minutes(tf)
        )

        # Skip if info timeframe is not higher than base
        if tf_minutes <= base_tf_minutes:
            continue

        # Keep only indicator columns (not OHLCV)
        ohlcv_cols = {"open", "high", "low", "close", "volume"}
        indicator_cols = [c for c in info_df.columns if c not in ohlcv_cols]
        if not indicator_cols:
            continue

        info_subset = info_df[indicator_cols].copy()

        # Rename columns with timeframe suffix
        rename_map = {col: f"{col}_{tf}" for col in indicator_cols}
        info_subset = info_subset.rename(columns=rename_map)

        # Shift timestamps forward by one base_tf unit to prevent look-ahead bias
        shift_delta = pd.Timedelta(minutes=base_tf_minutes)
        info_subset.index = info_subset.index + shift_delta

        # Forward fill within the info timeframe
        info_subset = info_subset.ffill()

        # Merge using asof (forward fill by time alignment)
        result = result.sort_index()
        info_subset = info_subset.sort_index()

        result = pd.merge_asof(
            result,
            info_subset,
            left_index=True,
            right_index=True,
            direction="backward",
        )

    return result
