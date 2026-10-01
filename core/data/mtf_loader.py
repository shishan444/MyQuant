"""Multi-timeframe data loading utilities.

Stateless functions shared by the evolution runner and the backtest API.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Set

import pandas as pd

from core.data.validation import validate_known_interval

logger = logging.getLogger(__name__)


# Timeframe aliases for parquet file lookup
_TF_ALIASES: dict[str, list[str]] = {
    "1h": ["1h", "60m"],
    "4h": ["4h"],
    "1d": ["1d", "1D"],
    "15m": ["15m"],
    "5m": ["5m"],
    "1m": ["1m"],
    "30m": ["30m"],
    "3d": ["3d"],
}


def safe_symbol(symbol: str) -> str:
    """symbol → 数据目录文件名段（find_parquet 的 safe_symbol 契约单一实现）。

    剥离非字母数字字符（"BTC-USDT" 与 "BTCUSDT" 定位同一文件）——
    消费方不各自内联正则（条件级高周期装载/登记可达探测等同族入口）。
    """
    return re.sub(r"[^A-Za-z0-9]", "", symbol)


def find_parquet(data_dir: Path, symbol_: str, timeframe: str) -> Optional[Path]:
    """Find parquet file for symbol+timeframe, trying known aliases."""
    # 读取路径周期须已知（TF_SECONDS 键集窄口径；与 csv_importer 导入侧
    # 宽口径 [0-9]+[mhdw] 的差异见 core/data/validation.py 契约注释）
    validate_known_interval(timeframe)
    primary = data_dir / f"{symbol_}_{timeframe}.parquet"
    if primary.exists():
        return primary

    for alias in _TF_ALIASES.get(timeframe, [timeframe]):
        alt = data_dir / f"{symbol_}_{alias}.parquet"
        if alt.exists():
            return alt
    return None


def load_and_prepare_df(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    data_start: Optional[str] = None,
    data_end: Optional[str] = None,
    min_bars: int = 50,
    stop_check: Optional[Callable[[], None]] = None,
    compute_indicators: bool = True,
) -> Optional[pd.DataFrame]:
    """Load a single-timeframe parquet, slice by date, optionally compute indicators.

    Returns None if data is unavailable or insufficient.
    When compute_indicators=False, returns raw OHLCV (+ derivatives) without
    feature columns — callers that need indicators must compute them themselves.
    This decouples core.data from core.features (data is the lower layer);
    default True preserves existing behaviour.
    """
    from core.data.storage import load_parquet

    symbol_ = safe_symbol(symbol)
    parquet_path = find_parquet(data_dir, symbol_, timeframe)
    if parquet_path is None:
        return None

    df = load_parquet(parquet_path)
    if df is None or len(df) < min_bars:
        return None

    if data_start:
        df = df[df.index >= data_start]
    if data_end:
        df = df[df.index <= data_end]
    if len(df) < min_bars:
        return None

    df = _try_merge_derivatives(data_dir, symbol_, timeframe, df)
    if compute_indicators:
        from core.features.indicators import compute_all_indicators
        return compute_all_indicators(df, stop_check=stop_check)
    return df


def load_mtf_data(
    data_dir: Path,
    symbol: str,
    exec_timeframe: str,
    enhanced_df: pd.DataFrame,
    needed_tfs: Set[str],
    data_start: Optional[str] = None,
    data_end: Optional[str] = None,
    min_bars: int = 50,
    stop_check: Optional[Callable[[], None]] = None,
    compute_indicators: bool = True,
) -> Optional[Dict[str, pd.DataFrame]]:
    """Load DataFrames for additional timeframes, optionally compute indicators.

    Returns a dict mapping timeframe -> DataFrame (including exec_timeframe).
    Returns None if no additional timeframes could be loaded.
    When compute_indicators=False, returns raw OHLCV frames without features.
    """
    from core.data.storage import load_parquet
    if compute_indicators:
        from core.features.indicators import compute_all_indicators

    symbol_ = safe_symbol(symbol)
    dfs_by_timeframe: Dict[str, pd.DataFrame] = {exec_timeframe: enhanced_df}

    for tf in needed_tfs:
        if tf == exec_timeframe:
            continue
        path = find_parquet(data_dir, symbol_, tf)
        if path is None:
            continue
        try:
            tf_df = load_parquet(path)
            if tf_df is None or len(tf_df) < min_bars:
                continue
            if data_start:
                tf_df = tf_df[tf_df.index >= data_start]
            if data_end:
                tf_df = tf_df[tf_df.index <= data_end]
            if len(tf_df) < min_bars:
                continue
            tf_df = _try_merge_derivatives(data_dir, symbol_, tf, tf_df)
            dfs_by_timeframe[tf] = (
                compute_all_indicators(tf_df, stop_check=stop_check)
                if compute_indicators else tf_df
            )
        except Exception as e:
            logger.warning("Failed to load MTF data for %s: %s", tf, e)
            continue

    return dfs_by_timeframe


def _try_merge_derivatives(
    data_dir: Path,
    safe_symbol: str,
    timeframe: str,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Try to load and merge OI/Funding Rate parquet into OHLCV DataFrame.

    Graceful no-op if derivative data files are absent.
    """
    from core.data.storage import load_parquet
    from core.data.derivatives_merger import merge_derivatives_into_ohlcv

    oi_df = None
    funding_df = None

    oi_path = data_dir / f"{safe_symbol}_oi_{timeframe}.parquet"
    if oi_path.exists():
        oi_df = load_parquet(oi_path)

    funding_path = data_dir / f"{safe_symbol}_funding.parquet"
    if funding_path.exists():
        funding_df = load_parquet(funding_path)

    if oi_df is not None or funding_df is not None:
        return merge_derivatives_into_ohlcv(df, oi_df, funding_df)

    return df
