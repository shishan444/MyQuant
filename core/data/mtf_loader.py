"""多周期数据装载底座（研究/回测/模拟盘共用）。

无状态函数集，从 Parquet 数据目录按 symbol+timeframe 装载 OHLCV
（含衍生列合并与指标计算）。全库最广消费：core/analysis 七模块、
core/evolution/evaluation、core/trading/runner、core/validation/mtf、
api/runner 与 api/routes（discovery/research/strategies）、scripts。
只读不写（数据保新归 core/data/updater）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Set

import pandas as pd

from core.data.validation import validate_known_interval

logger = logging.getLogger(__name__)


# Parquet 文件查找的周期别名表（历史文件命名变体，如 1h/60m、1d/1D）
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
    """查找 symbol+timeframe 的 Parquet 文件，主名未命中时尝试已知别名。

    Returns:
        命中路径；主名与别名均不存在时 None。

    Raises:
        ValueError: timeframe 不在 TF_SECONDS 键集（读取路径周期须已知）。
    """
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
    """装载单周期 Parquet，按日期切片，可选计算指标。

    数据不可得或切片后不足 min_bars 时返回 None。compute_indicators=False
    时返回原始 OHLCV（含衍生列）不带特征列——需要指标的调用方自行计算；
    这让 core.data 不依赖 core.features（数据是更底层），默认 True 保持
    既有行为。
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
) -> Dict[str, pd.DataFrame]:
    """装载附加周期 DataFrame，可选计算指标（多周期策略数据装配）。

    返回 timeframe → DataFrame 的映射，内含 exec_timeframe（调用方
    传入的 enhanced_df 原样入表）。附加周期不可得或不足 min_bars 时
    跳过该周期不报错（多周期策略按可得子集降级）；compute_indicators=
    False 时各帧为原始 OHLCV 不带特征列。
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
    """尝试装载并合并 OI/Funding Rate Parquet 进 OHLCV（衍生列装配）。

    衍生数据文件缺失时优雅直通（返回原 df，不阻塞装载）。
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
