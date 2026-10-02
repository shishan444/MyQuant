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

    from core.data.mtf_loader import find_parquet, safe_symbol  # B4.1：共享路径发现（含别名兜底）

    symbol_ = safe_symbol(pair)
    result: Dict[str, pd.DataFrame] = {}

    for tf in timeframes:
        path = find_parquet(Path(data_dir), symbol_, tf)
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
    """Merge higher-timeframe columns into base DataFrame（0.2.14 收口）.

    每列经 ``mtf_alignment.project_completed`` 值投影（asof_completed：
    高周期 bar T 的值挂载到收盘时刻 T+tf 后 backward 对齐）——旧
    「+1 基准 bar 位移」在非整除对齐下存在前视窗口（开盘后第 1 根
    基准 bar 即可见），退役；OHLCV 排除清单移除（``close_3d`` 等高
    周期基础列同样投影——「3d close > 3d EMA50」类条件真生效前提）。

    Column naming: ema_20 -> ema_20_4h（后缀列名与列序不变）。

    条件级 timeframe 键不再消费 merged 列（求值走 condition_kernel
    掩码投影路径）；本函数降格为 target 列引用投影器——服务
    ``"4h:ema_20"`` 冒号形制与显式后缀列名引用（Lab 前端零适配）。
    """
    from core.data.mtf_alignment import project_completed

    result = base_df.copy().sort_index()

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

        # 全列投影（OHLCV 排除清单移除——高周期基础列同样对齐）
        cols = list(info_df.columns)
        subset = info_df[cols].copy()

        # Rename columns with timeframe suffix
        rename_map = {col: f"{col}_{tf}" for col in cols}
        subset = subset.rename(columns=rename_map)

        projected = project_completed(
            subset, result.index, pd.Timedelta(minutes=tf_minutes))
        result = pd.concat([result, projected], axis=1)

    return result
